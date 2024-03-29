#!/usr/bin/python

import os, sys
import struct, time, functools
import fnmatch, glob, re
import fcntl, errno, select
import subprocess
import urllib.request, urllib.error, urllib.parse
import datetime
import pwd

import hashlib
from logging import warn, info, debug
from functools import reduce

lbu_cache_dir = os.environ.get("LBU_CACHE_DIR", os.path.expanduser("~/.cache/lbu") if os.getuid() else "/var/cache/lbu")
lbu_dir = os.path.dirname(__file__)

no_exceptions = os.environ.get("LBU_NO_EXCEPTIONS")

class CommandFailed(EnvironmentError): pass
class BuildAborted(RuntimeError): pass

class FilesystemError(LookupError): pass


class NotAufs(ValueError): pass
class NotLoopDev(ValueError): pass
class NotSFS(ValueError): pass
class BadArgumentsError(ValueError): pass

class UTC(datetime.tzinfo):
    def utcoffset(self, dt):
        return datetime.timedelta(0)

    def tzname(self, dt):
        return "UTC"

    def dst(self, dt):
        return datetime.timedelta(0)


def cached_property(fn):
    cached_attr_name="__cached__%s"%(fn.__name__,)

    @functools.wraps(fn)
    def getter(self):
        try: return getattr(self, cached_attr_name)
        except AttributeError: value=fn(self)
        setattr(self, cached_attr_name, value)
        return value
    getter.cached_property=True

    def setter(self, value): setattr(self, cached_attr_name, value)

    def deleter(self): delattr(self, cached_attr_name)
    return property(getter, setter, deleter)


def clear_cached_properties(obj):
    for prop in [p for p in [getattr(obj.__class__, n) for n in dir(obj.__class__)] if isinstance(p, property) and hasattr(obj, "__cached__" + p.fget.__name__)]:
        delattr(obj, prop.fget.__name__)


def repr_wrap(fn=None, as_str=False):
    if fn is None:
        def repr_wrap_gen(fn):
            return repr_wrap(fn, as_str)
        return repr_wrap_gen
    repr_fmt = "<%s.%s %s @%x>" if as_str else "<%s.%s %r @%x>"
    @functools.wraps(fn)
    def repr_gen(self):
        return repr_fmt%(self.__class__.__module__, self.__class__.__name__, fn(self), id(self))
    repr_gen._repr=fn
    return repr_gen


def cli_parse_argv(argv):
    kwargs = {}
    posargs = []
    kwarg_name = None
    for idx, arg in enumerate(argv):
        if arg.startswith("--"):
            try:
                eq_idx = arg.index('=')
            except ValueError:
                kwarg_name = arg[2:].replace('-', '_')
                if kwarg_name == '':
                    posargs.extend(argv[idx + 1:])
                    break
            else:
                kwarg_name = None
                kwargs[arg[2:eq_idx].replace('-', '_')] = arg[eq_idx + 1:]
        elif kwarg_name is not None:
            kwargs[kwarg_name] = arg
            kwarg_name = None
        else:
            posargs.append(arg)
    return (posargs, kwargs)


def cli_func(func=None, name=None, parse_argv=None, desc=None):
    if func is None:
        def gen(func_real):
            if name is not None: func_real._cli_name=name
            if parse_argv is not None: func_real._cli_parse_argv=parse_argv
            if desc is not None: func_real._cli_desc = desc
            return cli_func(func_real)
        return gen
    cli_func.commands[getattr(func, "_cli_name", func.__name__.replace("_", "-"))]=func
    if getattr(func, "_cli_parse_argv", None) is None:
        func._cli_parse_argv=cli_parse_argv

    def cli_call(argv):
        try: args, kwargs=func._cli_parse_argv(argv)
        except Exception as e:
            raise BadArgumentsError("bad arguments: %s"%e)
        try:
            debug("Calling: %s(*%r, **%r)", func.__name__, args, kwargs)
            return func(*args, **kwargs)
        except TypeError as e:
            if e.args[0].startswith('%s() '%(func.__name__,)):
                raise BadArgumentsError(e)
            else: raise
    func.cli_call=cli_call
    if not func.__doc__:
        import inspect
        spec=inspect.getfullargspec(func)
        rev_args=list(reversed(spec.args))
        defaults=dict([(rev_args[i_d[0]], i_d[1]) for i_d in enumerate(reversed(spec.defaults))]) if spec.defaults else {}
        func.__doc__=" ".join(["[<%s>=%r]"%(n, defaults[n]) if n in defaults else "<%s>"%n for n in spec.args]+
                              (["[<%s>...]"%spec.varargs] if spec.varargs else [])+
                              (["[<%s>=<value>...]"%spec.varkw] if spec.varkw else []))
    return func
cli_func.commands={}


@cli_func(name="help", desc="Show usage help for other commands")
def cli_func_help(command):
    return " ".join(map(str, ("Usage:", command, cli_func.commands[command].__doc__)))


def stamp2txt(stamp):
    return time.strftime("%Y%m%d_%H%M%S", time.localtime(stamp))


class CLIProgressReporter(object):
    nr_buckets=10
    output_stream=sys.stdout

    def __init__(self, full_size, **attrs):
        self.full_size=full_size
        self.report_buckets=[i * full_size / self.nr_buckets for i in range(self.nr_buckets)]
        list(map(lambda k: setattr(self, k, attrs[k]), attrs))

    def __call__(self, sz):
        if sz is None: print("100%", file=self.output_stream)
        elif self.report_buckets and sz>=self.report_buckets[0]:
            print("%d%%.."%(100 * sz / self.full_size), end=' ', file=self.output_stream)
            while self.report_buckets and sz>=self.report_buckets[0]:
                self.report_buckets.pop(0)
        self.output_stream.flush()

pr_cls = CLIProgressReporter

class TemplatedString(object):
    template = ""

    def __init__(self, **attrs):
        for k in attrs:
            setattr(self, k, attrs[k])

    def __str__(self):
        return self.template % self

    def __getitem__(self, item):
        try:
            return getattr(self, item)
        except AttributeError:
            return KeyError


class LXC(object):
    auto_remove = False
    init_cmd = []

    class BindEntry(object):
        @classmethod
        def from_strdef(cls, s):
            src, dst = s.split('=', 1)
            if dst.endswith(':ro'):
                dst, ro = dst[:-3], True
            else:
                ro = False
            return cls(src, dst, ro)
        def __init__(self, src, dst, ro=False):
            if isinstance(src, FSPath):
                src = src.path
            if isinstance(dst, FSPath):
                dst = dst.path
            self.src = src
            self.dst = dst.lstrip("/")
            self.ro = ro

        def __str__(self):
            return "%s=%s"%(self.src, self.dst)

        @repr_wrap(as_str=True)
        def __repr__(self):
            return "%r -> %r%s"%(self.src, self.dst, " [RO]" if self.ro else "")

    class Config(TemplatedString):
        template = """
lxc.uts.name = %(name)s
lxc.rootfs.path = %(rootfs)s
lxc.pty.max = 1024
%(apparmor_cfg)s

lxc.log.level = 1
lxc.autodev = %(autodev)s
lxc.mount.auto = proc sys

# use .drop instead of .keep if you want less restritive environment
%(cap_cfg)s
%(dev_cfg)s
%(extra_config)s
"""

        @cached_property
        def apparmor_profile(self):
            return os.environ.get("LXC_APPARMOR_PROFILE", "unconfined")

        @cached_property
        def apparmor_cfg(self):
            return "lxc.apparmor.profile = %s" % (self.apparmor_profile) if os.path.exists("/sys/kernel/security/apparmor") else ""

        @cached_property
        def dev_cfg(self):
            if self.devices_allow is None:
                return ""
            return "\n".join(["lxc.cgroup.devices.deny = a"] + ["lxc.cgroup.devices.allow = %s" % d for d in self.devices_allow])

        @cached_property
        def autodev(self):
            return os.environ.get("LXC_AUTODEV", "1")

        @cached_property
        def devices_allow(self):
            return ["c 1:8 r", "c 1:9 r", "c 1:5 r",
                    "c 1:3 rw", "c 1:7 rw",
                    "c 5:0 rw", "c 5:2 rw", "c 136:* rw",
                    "c 226:* rw", "c 116:* rw"]

        @cached_property
        def cap_cfg(self):
            if self.cap_keep:
                return "lxc.cap.keep = %s" % (" ".join(self.cap_keep, ))
            elif self.cap_drop:
                return "lxc.cap.drop = %s" % (" ".join(self.cap_drop, ))
            else:
                return ""

        @cached_property
        def cap_drop(self):
            return {"sys_module", "mac_admin", "mac_override", "sys_time"}

        @cached_property
        def cap_keep(self):
            return {"sys_chroot", "sys_admin", "dac_override", "chown fowner", "kill", "ipc_owner", "ipc_lock",
                    "setgid", "setuid", "sys_nice", "syslog", "lease", "dac_read_search", "audit_write", "setpcap",
                    "net_bind_service", "sys_resource", "net_broadcast", "net_admin", "net_raw"}

        @cached_property
        def lbu_cli(self):
            return FSPath(__file__).parent_directory.join("lbu_cli.py")

        @cached_property
        def rootfs(self):
            root_parts = []
            for part in self.all_parts:
                if isinstance(part, SFSFile):
                    root_parts.append(part.mounted_path.path)
                else:
                    root_parts.append(part.path)
            part.join("work").makedirs()
            part.join("data").makedirs()
            root_parts[-1:] = [part.join("data").path]
            return "overlayfs:" + ":".join(root_parts)

        @cached_property
        def extra_config(self):
            return "\n".join(map(str, self.extra_parts))

        class MountEntry(TemplatedString):
            template = "lxc.mount.entry = %(src_esc)s %(dst)s none %(opts)s 0 0"
            ro = False

            @cached_property
            def src(self):
                raise AttributeError("src property needs to be set")

            @cached_property
            def src_esc(self):
                return self.src.replace(" ", "\\040")

            @cached_property
            def opts(self):
                return ",".join(["bind", "create=dir"] + (["ro"] if self.ro else []))

            def __init__(self, src, dst, ro=False):
                TemplatedString.__init__(self, src=src, dst=dst, ro=ro)

        class VEth(TemplatedString):
            template = """
lxc.net.%(netnum)d.type = veth
lxc.net.%(netnum)d.flags = up
%(link_cfg)s
%(ip_cfg)s
%(gw_cfg)s
"""
            link = None
            ip = None
            gw = None

            @cached_property
            def link_cfg(self):
                return (("lxc.net.%(netnum)d.link = %(link)s") % self) if self.link else ""

            @cached_property
            def ip_cfg(self):
                return (("lxc.net.%(netnum)d.ipv4.address = %(ip)s") % self) if self.ip else ""

            @cached_property
            def gw_cfg(self):
                return (("lxc.net.%(netnum)d.ipv4.gateway = %(gw)s") % self) if self.gw else ""

            def __init__(self, link, ip=None, gw=None, netnum=0):
                TemplatedString.__init__(self, link=link, ip=ip, gw=gw, netnum=netnum)

        class VLan(TemplatedString):
            template = """
lxc.net.%(netnum)d.type = macvlan
lxc.net.%(netnum)d.macvlan.mode = bridge
lxc.net.%(netnum)d.flags = up
lxc.net.%(netnum)d.link = %(link)s
%(ip_cfg)s
%(gw_cfg)s"""
            ip = None
            gw = None

            @cached_property
            def ip_cfg(self):
                return (("lxc.net.%(netnum)d.ipv4.address = %(ip)s") % self) if self.ip else ""

            @cached_property
            def gw_cfg(self):
                return (("lxc.net.%(netnum)d.ipv4.gateway = %(gw)s") % self) if self.gw else ""

            def __init__(self, link, ip=None, gw=None, netnum=0):
                TemplatedString.__init__(self, link=link, ip=ip, gw=gw, netnum=netnum)

        def __init__(self, name, **attrs):
            self.name = name
            self.netnum = 0
            self.extra_parts = []
            TemplatedString.__init__(self, **attrs)

        def add_vlan(self, link, ip=None, gw=None):
            self.extra_parts.append(self.VLan(link, ip, gw, self.netnum))
            self.netnum+=1

        def add_veth(self, link, ip=None, gw=None):
            self.extra_parts.append(self.VEth(link, ip, gw, self.netnum))
            self.netnum+=1

        def add_bind(self, src, dst=None, ro=False):
            if isinstance(src, LXC.BindEntry):
                src, dst, ro = src.src, src.dst, src.ro
            if dst is None:
                dst = src.lstrip("/")
            self.extra_parts.append(self.MountEntry(src, dst, ro))

        def add_hostnet(self):
            self.extra_parts.append("lxc.net.%d.type=none" % (self.netnum, ))
            self.netnum+=1

    def __init__(self, name=None, **attrs):
        if name is None:
            name = "lxc-%d-%s" % (os.getpid(), time.time())
        self.name = name
        for k in attrs:
            setattr(self, k, attrs[k])

    def __del__(self):
        if self.auto_remove:
            if self.is_running:
                self.shutdown()
                while True:
                    time.sleep(0.1)
                    if not self.is_running:
                        break
            run_command(["lxc-destroy", "-n", self.name], as_user="root")

    @repr_wrap
    def __repr__(self):
        return self.name

    def get_status(self):
        status = run_command(["lxc-info", "-n", self.name], as_user="root")
        ret = {}
        last_link = {}
        for line in status.strip().split("\n"):
            k, v = line.split(":", 1)
            v=v.strip()
            if k=="Link":
                last_link=dict(name=v)
                ret.setdefault("Link", []).append(last_link)
            elif k.startswith(" "):
                last_link[k[1:]]=v
            else:
                last_link = {}
                ret[k]=v
        return ret

    @property
    def is_running(self):
        try: return self.get_status()["State"] == "RUNNING"
        except CommandFailed: return False

    @classmethod
    def from_sfs_ext(cls, name, sfs_parts, extra_parts=[], bind_dirs=[], **attrs):
        cmd = ["lxc-create", "-t", "sfs", "-n", name, "--",
               "--default-parts", " ".join(map(str, sfs_parts)), "--host-network"]
        cmd.extend(reduce(lambda a, b: a + ["--bind-ro" if b.ro else "--bind", str(b)], bind_dirs, []))
        cmd.extend(list(map(str, extra_parts)))
        run_command(cmd, as_user="root")
        if "auto_remove" not in attrs:
            attrs["auto_remove"] = True
        return cls(name, **attrs)

    @classmethod
    def from_sfs(cls, name, sfs_parts, bind_dirs=None, veth=None, vlan=None, nonet=False, **attrs):
        all_parts = attrs["all_parts"] = []
        for part in sfs_parts:
            if isinstance(part, FSPath):
                all_parts.append(part)
            elif FSPath(part).exists:
                all_parts.append(FSPath(part))
            else:
                found_part = None
                for part_s in part.split(','):
                    try:
                        found_part = sfs_finder[part_s]
                    except KeyError:
                        pass
                    else:
                        break
                if found_part is None:
                    raise KeyError("Cannot find LXC part %r" % (part,))
                all_parts.append(found_part)

        for part in all_parts:
            if isinstance(part, SFSFile):
                if part.mounted_path is None:
                    part.mount()
        cfg = LXC.Config(name, all_parts=all_parts)
        if "devices_allow" in attrs:
            cfg.devices_allow = attrs.pop("devices_allow")
        if veth is None and vlan is None and not nonet:
            cfg.add_hostnet()
        else:
            if veth is not None:
                for v in veth if isinstance(veth, list) else [veth]:
                    cfg.add_veth(*v)
            if vlan is not None:
                for v in vlan if isinstance(vlan, list) else [vlan]:
                    cfg.add_vlan(*v)
        if bind_dirs is not None:
            for bind_mnt in bind_dirs:
                cfg.add_bind(bind_mnt)
        cfg_file = FSPath("/var/lib/lxc/%s/config" % (name,))
        cfg_file.parent_directory.makedirs(sudo=True)
        with cfg_file.open("w") as cfg_f:
            cfg_f.write(str(cfg))
        return cls(name, **attrs)

    def start(self, init=None, foreground=False):
        cmd = ["lxc-start", "-n", self.name, "-F" if foreground else "-d", "-l", "info"]
        if init is None:
            init = self.init_cmd
        if init:
            cmd.append("--")
            cmd.extend(init)
        try:
            return run_command(cmd, as_user="root", env=dict(
                [(k, os.environ[k]) for k in [k for k in list(os.environ.keys()) if k.startswith("LXC_")]]))
        except CommandFailed as e:
            warn("Starting LXC instance %r failed: %r", self.name, e)
            if sys.stdin.isatty():
                breakpoint()
            raise

    def apt_install(self, packages):
        self.run(
            ["sh", "-x", "-c", 'dpkg -s "$@" >/dev/null || (apt-get update || apt-get update && apt-get install -y "$@" )', '_']+packages, show_output=True)

    def run(self, cmd, **args):
        if not self.is_running:
            self.start()
        try: return run_command(["lxc-attach", "-e", "-n", self.name, "--"] + cmd, as_user="root", **args)
        except CommandFailed as e:
            warn("Command %r failed with %d", cmd, e.args[1])
            args.setdefault("show_output", True)
            if "LXC_RUN_FAILSCRIPT" in os.environ:
                run_command(["sh", "-c", os.environ["LXC_RUN_FAILSCRIPT"], "_fail.sh", self.name] + cmd, **args)
            raise

    def shutdown(self):
        run_command(["lxc-stop", "-k", "-n", self.name], as_user="root")

def uniq_list(lst):
    return reduce(lambda a,b: a+[b] if len(a)==0 or a[-1]!=b else a, lst, [])

class SFSFinder(object):
    def __init__(self, sfs_list=None):
        if sfs_list is None:
            sfs_list = []
        self.sfs_list = []
        for sfs in sfs_list:
            self.register_sfs(sfs)

    @cached_property
    def _sfs_dirs(self):
        if "SFS_FIND_PATH" in os.environ:
            dirlist = os.environ["SFS_FIND_PATH"].split(":")
        else:
            dirlist = [FSPath(MountPoint(e["mnt"]).loop_backend).parent_directory.path for e in [e for e in global_mountinfo if e["fs_type"] == "squashfs"]]
        return dict([(p, SFSDirectory(p)) for p in [d for d in uniq_list(dirlist) if os.path.exists(d)]])

    def search_dirs(self, name, sfs_dirs=None):
        sfs_found = []
        if sfs_dirs is None:
            sfs_dirs = list(self._sfs_dirs.values())
        for sfs_dir in sfs_dirs:
            sfs_found.extend([sfs.curlink_sfs() for sfs in sfs_dir.find_all_sfs(name)])
        sfs_found.sort(key=lambda sfs: -sfs.create_stamp)
        if sfs_found:
            return sfs_found[0]

    def register_sfs(self, sfs):
        self.sfs_list.insert(0, sfs)

    def __getitem__(self, name):
        for sfs in self.sfs_list:
            if sfs.basename == name and sfs.exists:
                debug("SFSFinder(regs): %r -> %r", name, sfs.path)
                return sfs
        sfs = self.search_dirs(name)
        if sfs:
            self.register_sfs(sfs)
            debug("SFSFinder(dirs): %r -> %r", name, sfs.path)
            return sfs
        raise KeyError("Cannot find SFS", name)


sfs_finder = SFSFinder()


def _load_build_env(data):
    return [l.split("=", 1) for l in [v for v in data.split("\n") if v]]


class SFSBuilder(object):
    SFS_SRC_D = '/usr/src/sfs.d'
    GIT_SOURCE_PATH = os.path.join(SFS_SRC_D, '.git-source')
    GIT_COMMIT_PATH = os.path.join(SFS_SRC_D, '.git-commit')
    SQFS_EXCLUDE = os.path.join(SFS_SRC_D, ".sqfs-exclude")
    BUILD_ENV_PATH = os.path.join(SFS_SRC_D, ".env")

    LXC_PARTS_FILE = os.path.join(SFS_SRC_D, ".lxc-build-parts")
    LXC_DESTDIR = "/destdir"
    LXC_CACHE_DIR = "/var/cache/lbu"
    LXC_DL_CACHE = os.path.join(LXC_CACHE_DIR, "dl")
    LXC_LBU = "/opt/LiveBootUtils"
    LXC_INIT_CMD = ["sleep", os.environ.get("LXC_INIT_SLEEP", "7200")]

    SFS_BUILD_PROFILE_SH = os.path.join(lbu_dir, "scripts", "sfs_build_profile.sh")

    dest_dir_parent = os.path.join(lbu_cache_dir, "rebuild")
    default_lxc_parts = os.environ.get('LXC_BUILD_SFS_PARTS', '00-* scripts settings').split()

    def __init__(self, target_sfs, source=None):
        if not isinstance(target_sfs, SFSFile):
            target_sfs = SFSFile(target_sfs)
        self.target = target_sfs
        if source is None and target_sfs.exists:
            source = target_sfs.git_source
            try: self.run_env.update(_load_build_env(target_sfs.open_file(self.BUILD_ENV_PATH, "r").read()))
            except IOError: pass
            if source and target_sfs.git_branch and not "#" in source and urllib.parse.splittype(source)[0] in ("https", "http", "git"):
                source = "%s#%s"%(source, target_sfs.git_branch)
        if isinstance(source, str):
            if source.startswith("file://"):
                source = source[7:]
            if os.path.isdir(source) and os.path.exists(os.path.join(source, ".git")):
                source = GitRepo(source)
            else:
                source = dl.dl_file(source)
        self.source = source

    @cached_property
    def name(self):
        return "rebuild-%s.%d" % (self.target.basename.strip_down(), os.getpid())

    @cached_property
    def dest_base(self):
        dest_base = FSPath(os.path.join(self.dest_dir_parent, self.name), auto_remove=True)
        if not dest_base.exists:
            os.makedirs(dest_base.path, 0o755)
        return dest_base

    @cached_property
    def dest_dir(self):
        dest_dir = MountPoint(self.dest_base.join("destdir"), auto_remove=True)
        if dest_dir.is_mounted: return dest_dir
        if self.source is None:
            dest_dir.mount_combined([self.target])
            return dest_dir
        if os.environ.get("LXC_DESTDIR"):
            dest_dir = FSPath(os.environ["LXC_DESTDIR"])
        else:
            dest_dir.mount("destdir", fs_type="tmpfs", mode="0755")
        if self.source is not None:
            if not isinstance(self.source, GitRepo):
                raise ValueError("Source is not GitRepo")
            git_tar_out = ("cd \"$SRC\";git archive HEAD | tar x -C \"$DESTDIR\";"
                           "P=\"$(readlink -f .)\" git submodule --quiet foreach "
                           "'git archive --prefix=\"${PWD#$P/}/\" HEAD | tar x -C \"$DESTDIR\"'")
            src_uid = os.stat(self.source.path).st_uid
            run_command(["sh", "-c", git_tar_out],
                        env=dict(SUDO_UID="%d"%(src_uid), DESTDIR=dest_dir.path, SRC=self.source.path), as_user="root")
        return dest_dir

    @cached_property
    def lbu_d(self):
        return FSPath(__file__).parent_directory

    @cached_property
    def lxc_setup_d(self):
        d = MountPoint(self.dest_base.join("lxc-setup"), auto_remove=True)
        if d.is_mounted: return d
        d.mount("lxc-setup", fs_type="tmpfs", mode="0755")
        paths = [self.LXC_LBU, self.LXC_DL_CACHE, self.LXC_DESTDIR]
        paths.extend([h_l[1].lstrip("/") for h_l in self.deb_mappings])
        run_command(["mkdir", "-p"] + [d.join(sd).path for sd in paths], as_user="root")
        run_command(["cp", "--parents", "/etc/resolv.conf", d.path], as_user="root")
        return d

    @cached_property
    def lxc_rw_d(self):
        if os.environ.get("LXC_RW_D"): return FSPath(os.environ["LXC_RW_D"])
        d = MountPoint(self.dest_base.join("lxc-rw"), auto_remove=True)
        if d.is_mounted: return d
        d.mount("lxc-rw", fs_type="tmpfs", mode="0755")
        return d

    @cached_property
    def sfs_src_d(self):
        return self.dest_dir.join(self.SFS_SRC_D)

    @cached_property
    def lxc_parts(self):
        ret = None
        try: ret = self.dest_dir.open_file(self.LXC_PARTS_FILE, "r").read().strip().split()
        except IOError as e:
            if not e.errno == errno.ENOENT:
                raise
        if not ret:
            return self.default_lxc_parts[:]
        return ret

    @cached_property
    def deb_mappings(self):
        cache_dir = FSPath(dl.cache_dir)
        ret = [(cache_dir.join("archives"), "var/cache/apt/archives"),
               (cache_dir.join("lists"), "var/lib/apt/lists")]
        for p in ret:
            p[0].join("partial").makedirs()
        return ret

    @cached_property
    def bind_dirs(self):
        return [
            LXC.BindEntry(self.dest_dir, self.LXC_DESTDIR),
            LXC.BindEntry(dl.cache_dir, self.LXC_DL_CACHE),
            LXC.BindEntry(self.lbu_d, self.LXC_LBU, True),
        ] + [LXC.BindEntry(h_l1[0], h_l1[1]) for h_l1 in self.deb_mappings]

    @cached_property
    def lxc(self):
        lxc = LXC.from_sfs(self.name, self.lxc_parts + [d.path for d in [self.lxc_setup_d, self.lxc_rw_d]],
                           self.bind_dirs,
                           init_cmd=self.LXC_INIT_CMD, auto_remove=True)
        return lxc

    @cached_property
    def _run_env_def(self):
        return dict(
            dl.proxy_env,
            TERM=os.environ.get("TERM", "linux"),
            COLUMNS=os.environ.get("COLUMNS", "80"), LINES=os.environ.get("LINES", "25"),
            DESTDIR=self.LXC_DESTDIR,
            lbu=self.LXC_LBU,
            dl_cache_dir=self.LXC_DL_CACHE,
            SILENT_EXIT="1",
            HOME="/root",
            LANG="C.UTF-8")

    @cached_property
    def run_env(self):
        return dict(self._run_env_def)

    @property
    def run_env_mod(self):
        ret = self.run_env.copy()
        for k, v in list(self._run_env_def.items()):
            if k in ret and ret[k] == v:
                del ret[k]
            elif k not in ret:
                ret[k] = ""
        return ret

    def run_in_dest(self, cmd, **args):
        if not "env" in args:
            args["env"] = self.run_env
        return self.lxc.run(cmd, **args)

    def build_shell(self):
        try:
            run_command(["bash", "--rcfile", self.SFS_BUILD_PROFILE_SH, "-i"], cwd=self.dest_dir.path, show_output=True,
                        env=dict(self.run_env, sfs_build_target=self.target.basename, DESTDIR=self.dest_dir.path))
        except CommandFailed as e:
            warn("Build aborted from shell (exit status: %s)", e.args[1])
            raise BuildAborted()

    def build(self):
        apt_updated = False
        if os.environ.get("PRE_BUILD_SHELL"):
            self.build_shell()
        if "PRE_BUILD_SCRIPT" in self.run_env:
            run_command(["sh", "-c", self.run_env["PRE_BUILD_SCRIPT"], "_build.sh", self.dest_dir.path, self.lxc.name],
                        as_user="root", show_output=True, env=self.run_env)
        script = self.run_env.get("BUILD_SCRIPT")
        if "BUILD_SCRIPT" in self.run_env:
            self.run_in_dest(["sh", "-c", self.run_env["BUILD_SCRIPT"]], show_output=True)
        for script in sorted(self.sfs_src_d.walk(pattern="[0-9][0-9]-*"), key=lambda p: p.basename):
            if not apt_updated:
                self.run_in_dest(["apt-get", "update"], show_output=True)
                apt_updated = True
            before_build_script = os.environ.get(
                "BEFORE_BUILD_{0}".format(script.basename.replace("-","_").replace(".","_")))
            if before_build_script:
                self.run_in_dest(["sh", "-c", before_build_script], show_output=True)
            info("Running %s", script.basename)
            cmd = [os.path.join(self.LXC_DESTDIR, self.SFS_SRC_D.lstrip("/"), script.basename)]
            try: self.run_in_dest(cmd, show_output=True)
            except CommandFailed as e:
                warn("Script %r failed with %d", script.basename, e.args[1])
                if sys.stdin.isatty():
                    self.run_in_dest(["bash", "-i"], show_output=True)
                raise BuildAborted()
        if "LAST_BUILD_SCRIPT" in self.run_env:
            script = self.run_env.get("LAST_BUILD_SCRIPT")
            self.run_in_dest(["sh", "-c", self.run_env["LAST_BUILD_SCRIPT"]], show_output=True)
        if script is None and self.source is None:
            warn("No scripts found and no source given. No modifications will happen by default.")
            if sys.stdin.isatty():
                info("Modify $DESTDIR using interactive shell.")
                self.build_shell()
        if os.environ.get("POST_BUILD_SHELL"):
            self.build_shell()
        dst_temp = "%s.NEW.%s" % (self.target.path, os.getpid())
        cmd = ["mksquashfs", self.dest_dir.path, dst_temp, "-noappend"]
        if self.source is not None:
            git_source_url = self.source.source_url
            if git_source_url is not None:
                self.dest_dir.open_file(self.GIT_SOURCE_PATH, "w").write(git_source_url)
                self.dest_dir.open_file(self.GIT_COMMIT_PATH, "w").write(self.source.last_commit)
            if self.source.join(".git-facls").exists:
                try: self.run_in_dest(["sh", "-c", "cd \"$DESTDIR\"; setfacl --restore=.git-facls"])
                except CommandFailed as e:
                    warn("setfacl failed: %r", e.args[2])
            sqfs_excl = self.source.join(self.SQFS_EXCLUDE)
            if sqfs_excl.exists:
                cmd.extend(["-wildcards", "-ef", sqfs_excl.path])
        env_mod = list(self.run_env_mod.items())
        if env_mod:
            self.dest_dir.open_file(self.BUILD_ENV_PATH, "w").write("\n".join(["%s=%s" % (k_v[0], k_v[1]) for k_v in env_mod]))
        run_command(cmd, show_output=True)
        self.target.replace_file(dst_temp)
        sfs_finder.register_sfs(self.target)


class SFSDirectory(object):
    sfs_search_depth = int(os.environ.get("SFS_SEARCH_DEPTH", "3"))

    @repr_wrap
    def __repr__(self):
        return str(self.backend)

    def __init__(self, backend):
        if isinstance(backend, str):
            if os.path.isdir(backend) or backend.startswith('http://') or backend.startswith('https://'):
                backend = FSPath(backend, walk_pattern="*.sfs")
            elif os.path.isdir(os.path.dirname(backend)):
                backend = FSPath(os.path.dirname(backend),
                                 walk_pattern=os.path.basename(backend), walk_depth=0)

        if isinstance(backend, FSPath):
            self.backend = backend
        else:
            raise ValueError("Unknown backend: (%s) %r" % (type(backend).__name__, backend))

    @cached_property
    def all_sfs(self):
        return list(sorted(self.backend.walk(file_class=SFSFile, depth=self.sfs_search_depth), key=lambda s: s.basename))

    def join(self, *args, **kwargs):
        return self.backend.join(*args, **kwargs)

    def find_sfs(self, name):
        for sfs in self.all_sfs:
            if sfs.basename==name:
                return sfs

    def find_all_sfs(self, name):
        for sfs in self.all_sfs:
            if sfs.basename == name:
                yield sfs

    def prune_old_sfs(self):
        to_be_unlinked={}
        cur_sfs_lst = list(self.backend.walk(pattern='*.sfs', depth=0))
        for old_sfs in self.backend.walk(pattern="*.sfs.OLD*"):
            if old_sfs.exists:
                for cur_sfs in cur_sfs_lst:
                    if os.path.samefile(cur_sfs.path, old_sfs.path):
                        continue
            try: link_target = old_sfs.symlink_target
            except OSError: pass
            else:
                if not '/' in link_target:
                    is_current = False
                    old_tgt = old_sfs.parent_directory.join(link_target)
                    if old_tgt.exists:
                        for cur_sfs in old_sfs.parent_directory.walk(pattern='*.sfs', depth=0):
                            if os.path.samefile(cur_sfs.path, old_tgt.path):
                                is_current = True
                                break
                    if not is_current:
                        info("Unlinking: %s", old_tgt.path)
                        to_be_unlinked[old_tgt.path] = old_tgt
            info("Unlinking: %s", old_sfs.path)
            to_be_unlinked[old_sfs.path] = old_sfs
        for tgt in list(to_be_unlinked.values()):
            try: tgt.unlink()
            except OSError as e:
                warn("Could not unlink %r: %s", tgt.path, e)

class SFSDirectoryAufs(SFSDirectory):
    def __init__(self, backend='/'):
        if not isinstance(backend, MountPoint):
            backend = MountPoint(backend)
        SFSDirectory.__init__(self, backend)

    @cached_property
    def all_sfs(self):
        ret = []
        for component in self.backend.aufs_components:
            try:
                c_file = SFSFile(component.mountpoint.loop_backend)
            except NotLoopDev:
                continue
            try:
                c_file.validate_sfs()
            except NotSFS:
                continue
            ret.append(c_file.curlink_sfs(False))
        return ret

    def find_sfs(self, name):
        return SFSDirectory.find_sfs(self, name).curlink_sfs(True)


class FSPath(object):
    walk_hidden=False
    walk_depth = None
    walk_pattern = "*"
    walk_exclude = []
    _walk_func = staticmethod(os.walk)
    _remove_on_del = False

    def __new__(cls, path, **attrs):
        path_str = (path.path if isinstance(path, FSPath) else path if isinstance(path, str) else str(path))
        path_str = path_str.rstrip('.0123456789')
        if cls == FSPath and (path_str.endswith('.sfs') or path_str.endswith('.sfs.OLD')):
            cls=SFSFile
        if isinstance(path, str) and (path.startswith('http://') or path.startswith('https://')):
            cls = type('%s_url' % (cls.__name__,), (cls,), dict(
                file_size=cached_property(cls._url_file_size),
                open=cls._url_open, _walk_func=cls._url_walk
            ))
        return object.__new__(cls)

    def __init__(self, path, **attrs):
        if isinstance(path, FSPath): path=path.path
        if not isinstance(path, str):
            raise ValueError("Invalid init path type for %s: %s"%(self.__class__.__name__, type(path).__name__))
        self.path=path
        auto_remove = attrs.pop("auto_remove", False)
        if auto_remove:
            self._remove_on_del = auto_remove
        list(map(lambda k: setattr(self, k, attrs[k]), attrs))

    def join(self, *paths):
        return self.__class__(os.path.join(self.path, *[p.lstrip("/") for p in paths]))

    @property
    def exists(self):
        return os.path.exists(self.path)

    def islink(self):
        return os.path.islink(self.path)

    def lstat(self):
        return os.lstat(self.path)

    def stat(self):
        return os.stat(self.path)

    @cached_property
    def create_stamp(self):
        return int(os.stat(self.path).st_mtime)

    @cached_property
    def basename(self): return os.path.basename(self.path)

    @repr_wrap
    def __repr__(self): return self.path

    @cached_property
    def backend(self):
        if self.mountpoint.fs_type=="aufs":
            for mpt in [c.mountpoint for c in reversed(self.mountpoint.aufs_components)]:
                if self.path in mpt: return FSPath(mpt.loop_backend)
        raise RuntimeError("Cannot determine backend of file", self.path)

    @cached_property
    def aufs_original(self):
        if not self.mountpoint.fs_type == 'aufs':
            raise NotAufs('Not located at aufs mountpoint')
        for aufs_part in self.mountpoint.aufs_components:
            test_file = aufs_part.join(self.path)
            if test_file.exists:
                return test_file

    @cached_property
    def component_files(self):
        ret = []
        for fs_part in self.mountpoint.fs_components:
            test_file = fs_part.join(self.path)
            if test_file.exists:
                ret.append(test_file)
            else:
                warn("file %r does not exist", test_file.path)
        return tuple(ret)

    @cached_property
    def parent_directory(self):
        return FSPath(self._parent_path)

    def __eq__(self, other):
        if isinstance(other, FSPath):
            return self.path == other.path
        elif isinstance(other, str):
            return self.path == other
        else:
            return super(FSPath, self) == other

    def __str__(self): return self.path

    def open(self, mode="rb"):
        return open(self.path, mode)

    def _url_open(self, mode="rb"):
        return urllib.request.urlopen(self.path)

    _href_re = re.compile(rb'<a\b[^>]*\bhref=([^\s>]+)[^>]*>')
    _proto_re = re.compile(r'^\w+:')
    def _url_walk(self, path):
        resp = urllib.request.urlopen(path)
        if not resp.code == 200:
            raise BadArgumentsError("Status code not OK: %s %s" % (resp.code, resp.msg))
        if not resp.headers.get_content_type() == "text/html":
            raise BadArgumentsError("not text/html: %r"%(resp.type))
        dir_names = []
        file_names = []
        for href in self._href_re.findall(resp.read()):
            href = href.decode("utf8")
            if href.startswith('"') or href.startswith("'"):
                href = href[1:-1]
            if href.startswith('/') or href == "." or href == ".." or self._proto_re.match(href):
                continue
            if href.endswith('/'): dir_names.append(href)
            else : file_names.append(href)
        yield path, dir_names, file_names


    def makedirs(self, mode=0o755, sudo=False):
        if not self.exists:
            if sudo:
                run_command(["mkdir", "-m", "%04o"%(mode), "-p", self.path], as_user="root")
            else:
                self.parent_directory.makedirs(mode)
                os.mkdir(self.path, mode)

    def realpath(self):
        return FSPath(os.path.realpath(self.path))

    def walk(self, pattern=None, file_class=None, exclude=None, depth=None):
        if pattern is None: pattern = self.walk_pattern
        if isinstance(pattern, str):
            pattern = pattern.split(",")
        if exclude is None: exclude = self.walk_exclude
        if isinstance(exclude, str):
            exclude = exclude.split(",")
        if file_class is None: file_class=FSPath
        if depth is None: depth=self.walk_depth
        for d, dn, fn in self._walk_func(self.path):
            if depth is not None and d.count('/') - self.path.count('/') == depth:
                dn[:] = []
            if not self.walk_hidden:
                dn[:]=[x for x in dn if not x.startswith(".")]
                fn[:]=[x for x in fn if not x.startswith(".")]
            for f in [n for n in fn if any([fnmatch.fnmatch(n, pat) for pat in pattern])]:
                if any([fnmatch.fnmatch(f, pat) for pat in exclude]):
                    continue
                yield file_class(os.path.join(d, f))

    @cached_property
    def file_info(self):
        ret={}
        try: st=os.stat(self.path)
        except OSError:
            try: st=os.lstat(self.path)
            except OSError: pass
            else:
                ret["mtime"]=datetime.datetime.fromtimestamp(st.st_mtime, UTC()).isoformat()
                ret["symlink"] = os.readlink(self.path)
        else:
            ret["size"] = st.st_size
            ret["mtime"] = datetime.datetime.fromtimestamp(st.st_mtime, UTC()).isoformat()
        try: ret["mtime"] = datetime.datetime.fromtimestamp(self.create_stamp, UTC()).isoformat()
        except IOError: pass
        return ret

    @cached_property
    def file_tree(self):
        ret={}
        orig_path=self.path.rstrip('/').split(os.path.sep)
        for f in self.walk():
            path_parts = f.parent_directory.path.split(os.path.sep)[len(orig_path):]
            dir_entry = reduce(lambda a, b: a.setdefault("dirs", {}).setdefault(b, {}), path_parts, ret)
            dir_entry.setdefault("files", {})[f.basename] = f.file_info
        return ret

    @cached_property
    def file_size(self): return os.stat(self.path).st_size

    @cached_property
    def fobj(self):
        return self.open()

    def _url_file_size(self):
        clen = self.fobj.headers.get("Content-Length")
        if clen is not None:
            return int(clen)

    @cached_property
    def mountpoint(self):
        orig_dev=self.lstat().st_dev
        path_components=(self.path if self.islink() else self.realpath().path).split(os.path.sep)
        sub_paths=[FSPath(os.path.sep.join(path_components[:n+1]) or os.path.sep) for n in range(len(path_components))]
        cur_path=self.path
        for test_path in reversed(sub_paths):
            if not test_path.lstat().st_dev==orig_dev:
                break
            cur_path=test_path
        return MountPoint(cur_path)

    @cached_property
    def _parent_path(self):
        parent_path=os.path.sep.join(os.path.realpath(self.path).rsplit(os.path.sep, 1)[:-1])
        if not parent_path: parent_path=os.path.sep
        return parent_path

    def open_file(self, path, mode="rb"):
        if not self.exists and mode[:1] in "wa":
            os.makedirs(self.path, 0o755)
        return open(os.path.join(self.path, path.lstrip("/")), mode)

    @property
    def symlink_target(self): return os.readlink(self.path)

    def replace_file(self, temp_filename, change_stamp=None, backup_name=None):
        is_link=os.path.islink(self.path)
        try: old_stat=os.stat(self.path)
        except OSError: old_stat = None
        if backup_name is None: backup_name="%s.OLD.%s"%(self.path, int(time.time()))
        if is_link or self.exists:
            os.rename(self.path, backup_name)
        if change_stamp is None:
            change_stamp=os.stat(temp_filename).st_mtime
        new_name="%s.%s"%(self.path, int(change_stamp))
        os.rename(temp_filename, new_name)
        try:
            if os.environ.get("NO_SFS_SYMLINKS") or self.parent_directory.join(".nolinks").exists:
                os.rename(new_name, self.path)
            else:
                os.symlink(os.path.basename(new_name), self.path)
        except OSError as e:
            if e.errno == errno.EPERM:
                os.rename(new_name, self.path)
            else: raise
        if old_stat is not None:
            try: os.chown(self.path, old_stat.st_uid, os.stat(self.path).st_gid)
            except OSError: pass
            try: os.chown(self.path, os.stat(self.path).st_uid, old_stat.st_gid)
            except OSError: pass
            try: os.chmod(self.path, old_stat.st_mode)
            except OSError as e:
                warn("Failed to change new file mode to %o: %s", old_stat.st_mode, e)
        clear_cached_properties(self)

    def unlink(self):
        os.unlink(self.path)

    @property
    def loop_dev(self):
        try: alt_path = self.aufs_original.path
        except NotAufs: alt_path = self.path
        for devname in os.listdir('/sys/block'):
            if not devname.startswith('loop'):
                continue
            try: bfile = open('/sys/block/%s/loop/backing_file'%(devname,)).read().strip()
            except IOError:
                continue
            if not os.path.exists(bfile):
                continue
            if not os.path.samefile(bfile, self.path) and not os.path.samefile(bfile, alt_path):
                continue
            if not int(open('/sys/block/%s/loop/offset'%(devname,)).read().strip())==0:
                continue
            return '/dev/%s'%(devname,)

    def __del__(self):
        if self._remove_on_del and self.exists:
            if os.path.islink(self.path):
                try: os.unlink(self.path)
                except OSError as e:
                    warn("Cannot unlink %r: %s", self.path, e)
            elif os.path.isdir(self.path):
                try: os.rmdir(self.path)
                except OSError as e:
                    if not e.errno==errno.ENOTEMPTY:
                        warn("Cannot rmdir %r: %s", self.path, e)
            else:
                raise ValueError("Refuse auto-remove files", self)


class MountInfo(object):
    def __init__(self, mountinfo='/proc/self/mountinfo'):
        self.minfo = mountinfo
        self._mnt_cache = {}

    def __iter__(self):
        with open(self.minfo) as minfo_fobj:
            for line in minfo_fobj:
                ret = self.proc_mountinfo_line(line)
                yield ret
    
    @staticmethod
    def decode_escapes(s):
        try:
            return s.decode('string_escape')
        except (AttributeError,LookupError):
            import codecs
            return codecs.escape_decode(s.encode('utf8'))[0].decode('utf8')

    @staticmethod
    def proc_mountinfo_line(line):
        parts=line.rstrip('\n').split(' ')
        ret = dict(mount_id=int(parts[0]), parent_id=int(parts[1]),
                   st_dev=reduce(lambda a, b: (a<<8)+b, list(map(int, parts[2].split(':')))),
                   root=MountInfo.decode_escapes(parts[3]), mnt=MountInfo.decode_escapes(parts[4]),
                   opts_mnt=set(parts[5].split(",")), opt_fields=set())
        idx=6
        while not parts[idx]=='-':
            ret['opt_fields'].add(parts[idx])
            idx+=1
        ret["fs_type"] = parts[idx+1]
        ret["dev"] = None if parts[idx+2]=='none' else parts[idx+2]
        ret["opts"] = set(parts[idx+3].split(","))
        return ret

    @cached_property
    def entries(self):
        return list(self)

    def find_dev(self, dev_name=None, dev_id=None):
        if dev_id is None:
            dev_id = os.stat(dev_name).st_rdev
        for entry in self.entries:
            if entry["st_dev"] == dev_id:
                return entry


global_mountinfo = MountInfo()


class GitRepo(FSPath):
    @cached_property
    def last_commit(self):
        return run_command(['git', 'log', '-1', '--format=%H'], cwd=self.path)

    @cached_property
    def source_url(self):
        try: remote, branch = run_command(["git", "rev-parse", "--abbrev-ref", "@{upstream}"], cwd=self.path).split("/")
        except CommandFailed:
            return None
        return "%s#%s" % (run_command(["git", "config", "--get", "remote.%s.url" % (remote,)], cwd=self.path), branch)

    @cached_property
    def last_stamp(self):
        return int(run_command(['git', 'log', '-1', '--format=%ct'], cwd=self.path))


def parse_time(s, fmt, tz="GMT"):
    parsed_time = time.strptime(s, fmt)
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    stamp = time.mktime(parsed_time)
    if old_tz is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = old_tz
    time.tzset()
    return stamp


class Downloader(object):
    git_url_re = re.compile(r'(^git://.*?|^git\+.*?|.*?\.git)(?:#(?P<branch>.*))?$')
    http_url_re = re.compile(r'^https?://.*')

    http_recv_tmout = 10
    http_read_size = 8192
    http_time_format = "%a, %d %b %Y %H:%M:%S GMT"

    _env_proxy_vars = " ".join([n for n in list(os.environ.keys()) if n.lower().endswith('_proxy')])
    pass_host_env = os.environ.get("DL_PROXY_ENV_VARS", _env_proxy_vars).split()

    @cached_property
    def proxy_env(self):
        return dict([(n, os.environ[n]) for n in [n for n in self.pass_host_env if n in os.environ]])

    @cached_property
    def cache_dir(self):
        cache_dir = os.environ.get("dl_cache_dir", os.path.join(lbu_cache_dir, "dl"))
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, 0o755)
        return cache_dir

    def dl_file_git(self, source, dest_path):
        git_m = self.git_url_re.match(source)
        git_branch = git_m.group('branch')
        if git_branch is not None:
            source = source[:git_m.start('branch') - 1]
            if dest_path.endswith('#%s' % (git_branch,)):
                dest_path = dest_path[:-len(git_branch) - 1]
            dest_path = '%s@%s' % (dest_path, git_branch)

        if source[:4] == 'git+':
            source = source[4:]
        git_env = dict(self.proxy_env)
        if "SSH_AUTH_SOCK" in os.environ:
            git_env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
        if os.path.exists(dest_path):
            cmd = ['git', 'pull', '--recurse-submodules', source]
            if git_branch: cmd += [git_branch]
            try: run_command(cmd, cwd=dest_path, env=git_env)
            except CommandFailed as e:
                warn("Update failed, will use old cache for %r. Error message: %r", dest_path, e.args[2])
                return GitRepo(dest_path)
            if os.path.exists(os.path.join(dest_path, '.gitmodules')):
                run_command(['git', 'submodule', 'update', '--depth', '1'], cwd=dest_path, env=git_env)
            return GitRepo(dest_path)
        else:
            cmd = ['git', 'clone', '--recurse-submodules']
            if git_branch: cmd += ['-b', git_branch]
            cmd += ['--depth=1', source, dest_path]
            run_command(cmd, env=git_env)
            return GitRepo(dest_path)

    def dl_file_url(self, source, dest_path):
        opener = urllib.request.build_opener()
        if os.path.exists(dest_path):
            dest_st = os.stat(dest_path)
            if dest_st.st_size > 0:
                opener.addheaders.append(("If-Modified-Since",
                                          time.strftime(self.http_time_format, time.gmtime(dest_st.st_mtime))))
        try:
            url_f = opener.open(source)
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return FSPath(dest_path)
            raise

        dest_path_tmp = "%s.%d.dltemp" % (dest_path, os.getpid())
        dest_f = open(dest_path_tmp, "wb")
        while True:
            try:
                url_f.fileno()
            except AttributeError:
                pass
            else:
                r_in = select.select([url_f], [], [], self.http_recv_tmout)[0]
                if not r_in:
                    info("No data in %s seconds, stalled?", self.http_recv_tmout)
                    continue
            d = url_f.read(self.http_read_size)
            if not d:
                break
            dest_f.write(d)
        dest_f.close()
        lm_hdr = url_f.headers.get("Last-Modified")
        if lm_hdr:
            mtime = parse_time(lm_hdr, self.http_time_format, "GMT")
            os.utime(dest_path_tmp, (time.time(), mtime))
        os.rename(dest_path_tmp, dest_path)
        return FSPath(dest_path)

    def dl_file(self, source, fname=None, dest_dir=None):
        if dest_dir is None:
            dest_dir = self.cache_dir
        if fname is None:
            fname = "%s-%s" % (hashlib.md5(source.encode('utf-8')).hexdigest()[:8], os.path.basename(source))
            if fname.endswith('.git'):
                fname = fname[:-4]
        dest = os.path.join(dest_dir, fname)

        if self.git_url_re.match(source):
            return self.dl_file_git(source, dest)
        return self.dl_file_url(source, dest)


dl = Downloader()

class ChecksumFile(FSPath):
    std_name = "sha256sum.txt"

    def update(self, target, checksum):
        relpath = os.path.relpath(target.path, self.parent_directory.path)
        with self.open("r+") as f:
            while True:
                pos = f.tell()
                line = f.readline()
                if line=='':
                    print(f"{checksum}  {relpath}", file=f)
                    break
                f_checksum, f_name = line.split()
                if f_name == relpath:
                    if f_checksum!=checksum:
                        f.seek(pos)
                        f.write(checksum)
                    break

    @classmethod
    def find_in_parents_of(cls, target):
        fs_dir = target.parent_directory
        while True:
            test_file = fs_dir.join(cls.std_name)
            if test_file.exists:
                return cls(test_file.path)
            fs_dir=fs_dir.parent_directory
            if not fs_dir.path or fs_dir.path=="/":
                break

class SFSFile(FSPath):
    UPTDCHECK_PATH = os.path.join(SFSBuilder.SFS_SRC_D, '.check-up-to-date')
    GIT_SOURCE_PATH = SFSBuilder.GIT_SOURCE_PATH
    GIT_COMMIT_PATH = SFSBuilder.GIT_COMMIT_PATH
    BUILD_ENV_PATH = SFSBuilder.BUILD_ENV_PATH
    PARTS_DIR='/.parts'

    progress_cb=None
    chunk_size=8192
    fsync_size=int(os.environ.get('SFS_FSYNC_SIZE', '0x1000000'), 0)
    auto_unmount = False
    checksum_algo = hashlib.sha256
    checksum_file = None

    class SFSBasename(str):
        def strip_down(self):
            ret=self[3:] if fnmatch.fnmatch(self, "[0-9][0-9]-*") else self[:]
            try: ret=ret[:ret.rindex(".sfs")]
            except ValueError: pass
            return ret

        def prio(self):
            if fnmatch.fnmatch(self, "[0-9][0-9]-*"):
                return int(self[:2])
            else:
                return None

        @repr_wrap(as_str=True)
        def __repr__(self):
            return "[%s] %r"%(self.prio(), self.strip_down())

        def __eq__(self, other):
            if super(SFSFile.SFSBasename, self) == other: return True
            #print "Comparing %s to %r"%(self, other)
            other = SFSFile.SFSBasename(other)
            other_prio = other.prio()
            if other_prio is not None:
                self_prio = self.prio()
                if self_prio is not None and not self_prio == other_prio:
                    #print "Priority mismatch"
                    return False
            if self.strip_down()==other.strip_down():
                return True
            try: without_sfs = self[:self.rindex('.sfs')]
            except ValueError: without_sfs = self
            if fnmatch.fnmatch(without_sfs, str(other)):
                return True
            return False

    def validate_sfs(self):
        if not os.path.isfile(self.path): return False
        return self.open().read(4) == b"hsqs"

    fn_ts_re = re.compile(r'^(.+?)(?:(\.OLD)?\.([0-9]+))+(?: \(deleted\))?$')

    def curlink_sfs(self, prefer_newlink=True):
        """prefer_newlink: prefer current properly named symlink over actually same file"""
        ts_m = self.fn_ts_re.match(self.path)
        if ts_m:
            linkname = ts_m.group(1)
            if os.path.exists(linkname):
                if prefer_newlink or os.path.samefile(linkname, self.path):
                    return SFSFile(linkname)
        return self

    @cached_property
    def sfs_directory(self):
        if not self.validate_sfs(): raise NotSFS("Not a SFS file", self.path)
        return SFSDirectory(self._parent_path)

    @cached_property
    def basename(self): return self.SFSBasename(super(SFSFile, self).basename)

    @cached_property
    def create_stamp(self):
        return self._get_create_stamp(self.open().read(12))

    @staticmethod
    def _get_create_stamp(header):
        return struct.unpack("<L", header[8:12])[0]

    @cached_property
    def mounted_path(self): # pylint: disable=method-hidden
        ldev = self.loop_dev
        if ldev is None: return
        mentry = global_mountinfo.find_dev(ldev)
        if mentry is None: return
        return MountPoint(mentry["mnt"])

    @cached_property
    def git_source(self):
        try: git_source = self.open_file(self.GIT_SOURCE_PATH, "r").read().strip()
        except IOError: return
        if '#' in git_source:
            git_source, self._git_branch = git_source.rsplit('#', 1)
        else:
            self._git_branch = None
        return git_source

    @cached_property
    def git_commit(self):
        try: return self.open_file(self.GIT_COMMIT_PATH, "r").read().strip()
        except IOError: pass

    @cached_property
    def git_branch(self):
        if self.git_source is None: return
        try: return self._git_branch
        except AttributeError: pass

        # should be executed quite rarely..
        return self.open_file(self.GIT_SOURCE_PATH).read().strip().rsplit('#', 1)[1]

    @cached_property
    def git_repo(self):
        return dl.dl_file(self.git_source if self.git_branch is None else '%s#%s' % (
            self.git_source, self.git_branch))

    @cached_property
    def latest_stamp(self):
        if self.git_source:
            if not self.git_commit == self.git_repo.last_commit:
                return self.git_repo.last_stamp
        try: self.open_file(self.UPTDCHECK_PATH)
        except IOError:
            return self.create_stamp

        if self.mounted_path == None:
            self.mount()
        try:
            run_env = dict(dl.proxy_env, DESTDIR=self.mounted_path.path, dl_cache_dir=dl.cache_dir,
                           lbu=FSPath(__file__).parent_directory.path)
            try: run_env.update(_load_build_env(self.open_file(self.BUILD_ENV_PATH, "r").read()))
            except IOError: pass
            run_command([self.mounted_path.join(self.UPTDCHECK_PATH).path],
                        show_output=True, env=run_env)
        except CommandFailed:
            return int(time.time())
        return self.git_repo.last_stamp if self.git_source else self.create_stamp

    def open_file(self, path, *args, **kwargs):
        if self.mounted_path == None:
            self.mount()
        return self.mounted_path.open_file(path, *args, **kwargs)

    def mount(self, mountdir=None, auto_remove=True):
        if mountdir is None:
            mountdir = os.path.join(self.PARTS_DIR, "%02d-%s.%d" % (
                (lambda x: 99 if x is None else x)(self.basename.prio()),
                self.basename.strip_down(), self.create_stamp))
            if not os.path.exists(mountdir):
                run_command(['mkdir', '-p', mountdir], as_user='root')
        mnt = MountPoint(mountdir)
        if not mnt.is_mounted:
            try:
                path = self.aufs_original.path
            except NotAufs:
                path = self.path
            mnt.mount(path, "loop", "ro", auto_remove=auto_remove)
        self.mounted_path = mnt
        return mnt

    def rebuild_and_replace(self, source=None, env=None):
        builder = SFSBuilder(self, source)
        extra_binds = os.environ.get("BUILD_EXTRA_BINDS")
        if extra_binds:
            for bind_def in extra_binds.split(" "):
                builder.bind_dirs.append(LXC.BindEntry.from_strdef(bind_def))
        if env is not None:
            builder.run_env.update(**env)
        builder.build()

    def replace_with(self, other, progress_cb=None):
        dst_temp="%s.NEW.%s"%(self.path, os.getpid())
        dst_fobj=open(dst_temp, "wb")
        create_stamp=None
        not_synced=0
        checksum = self.checksum_algo()
        with other.open() as src_fobj:
            nbytes=0
            if progress_cb: progress_cb(nbytes)
            while True:
                data=src_fobj.read(self.chunk_size)
                if not data:
                    break
                if create_stamp is None:
                    create_stamp=self._get_create_stamp(data)
                nbytes+=len(data)
                dst_fobj.write(data)
                not_synced+=len(data)
                checksum.update(data)
                if self.fsync_size>0 and not_synced>=self.fsync_size:
                    os.fsync(dst_fobj.fileno())
                    not_synced=0
                if progress_cb: progress_cb(nbytes)
            if progress_cb: progress_cb(None)
        if self.fsync_size>0 and not_synced>0:
            os.fsync(dst_fobj.fileno())
        dst_fobj.close()
        self.replace_file(dst_temp, create_stamp)
        info("File digest: %s", checksum.hexdigest())
        if self.checksum_file:
            self.checksum_file.update(self, checksum.hexdigest())
        sfs_finder.register_sfs(self)

    @cached_property
    def needs_update(self):
        return self.latest_stamp > self.create_stamp


_mount_tab=None


def _load_mount_tab():
    global _mount_tab
    _mount_tab=[l.rstrip("\n").split() for l in reversed(list(open("/proc/mounts")))]


class MountPoint(FSPath):
    default_combined_fs_type = os.getenv("COMBINED_MOUNT_TYPE", "overlay")

    def __del__(self):
        if self._remove_on_del:
            self.umount()
        if MountPoint is not None:
            super(MountPoint, self).__del__()

    @cached_property
    def fs_type(self): return self.mountinfo['fs_type']

    @cached_property
    def mount_source(self): return self.mountinfo['dev']

    @cached_property
    def mount_options(self): return self.mountinfo['opts']

    def umount(self):
        if not self.exists or not self.is_mounted:
            return
        run_command(["umount", "-l", self.path], as_user="root")

    def mount(self, src, *opts, **kwargs):
        if not os.path.exists(self.path):
            os.makedirs(self.path, 0o755)
        cmd=["mount", src, self.path]
        if kwargs.pop("bind", False):
            cmd.append("--bind")
        fs_type = kwargs.pop("fs_type", False)
        if fs_type:
            cmd.extend(["-t", fs_type])
        auto_remove = kwargs.pop("auto_remove", False)
        if auto_remove:
            self._remove_on_del = True
        if opts or kwargs:
            cmd.extend(["-o", ",".join(list(opts) + ["%s=%s"%(k, kwargs[k]) for k in kwargs])])
        run_command(cmd, as_user='root')

    def remove_on_delete(self, value=True):
        self._remove_on_del = value

    @property
    def is_mounted(self):
        if not self.exists: return False
        try: del self.mountinfo
        except AttributeError: pass
        return self.mountinfo is not None

    @cached_property
    def mountinfo(self):
        for e in global_mountinfo:
            try:
                if os.path.samefile(e["mnt"], self.path):
                    return e
            except OSError:
                continue

    @cached_property
    def aufs_si(self):
        return list(filter(lambda x: x.startswith("si="), self.mount_options))[0].split("=")[1]

    @cached_property
    def aufs_components(self):
        if not self.fs_type=="aufs": raise NotAufs("Mountpoint is not aufs", self.path)
        components=[]
        glob_prefix="/sys/fs/aufs/si_%s/br"%(self.aufs_si,)
        for branch_file in sorted(glob.glob(glob_prefix + "[0-9]*"), key=lambda v: int(v[len(glob_prefix):])):
            branch_dir, branch_mode=open(branch_file).read().strip().rsplit("=", 1)
            components.append(FSPath(branch_dir, aufs_mode=branch_mode, aufs_index=int(branch_file[len(glob_prefix):])))
        return components
    
    @cached_property
    def overlay_components(self):
        dirs = []
        for opt in self.mount_options:
            if opt.startswith("lowerdir="):
                dirs.extend(map(lambda d: FSPath(d), opt[9:].split(":")))
            elif opt.startswith("upperdir="):
                dirs.insert(0, FSPath(opt[9:]))
        return dirs

    @cached_property
    def fs_components(self):
        if self.fs_type=="aufs": return self.aufs_components
        elif self.fs_type=="overlay": return self.overlay_components
        else: raise NotAufs("Need aufs or overlay mountpoint")

    def __contains__(self, item):
        if isinstance(item, str):
            ret=os.path.exists(os.path.join(self.path, "./" + item))
            return ret
        raise ValueError("Unknown item type", type(item).__name__)

    @cached_property
    def loop_backend(self):
        source = self.mount_source
        if source is None:
            raise NotLoopDev("No source device registered")
        loop_name=source.split(os.path.sep)[-1]
        if not loop_name.startswith("loop"): raise NotLoopDev("Mountpoint does not seem to be loop device", loop_name)
        return open(os.path.join("/sys/block", loop_name, "loop/backing_file")).read().rstrip("\n")

    def mount_combined(self, parts, **kwargs):
        dirs = []
        automounted_sfs = []
        for part in parts:
            if isinstance(part, str):
                if '/' in part and os.path.exists(part):
                    part = FSPath(part)
                else:
                    part = sfs_finder[part]
            if isinstance(part, SFSFile):
                if not part.mounted_path:
                    part.mount()
                    automounted_sfs.append(part)
                part = part.mounted_path
            dirs.append(part.path)
        if os.environ.get("LXC_RW_D"):
            rw_mount = FSPath(os.environ["LXC_RW_D"])
            try: dirs.remove(rw_mount.path)
            except ValueError: pass
        else:
            rw_mount = MountPoint(FSPath(lbu_cache_dir).join("comnt-rw-%s-%s"%(os.getpid(), time.time())))
            rw_mount.mount("comnt-rw", fs_type="tmpfs", mode="0755")
        kwargs.setdefault("fs_type", self.default_combined_fs_type)
        if kwargs["fs_type"] == "aufs":
            dirs_arg = ":".join(["%s=rw" % (rw_mount.path,)] + ["%s=ro" % (d,) for d in reversed(dirs)])
            kwargs.setdefault("dirs", dirs_arg)
        elif kwargs["fs_type"] == "overlay":
            kwargs.setdefault("lowerdir", ":".join(reversed(dirs)))
            rw_mount.join("upper").makedirs(sudo=True)
            kwargs.setdefault("upperdir", "%s/upper" % (rw_mount.path,))
            rw_mount.join("work").makedirs(sudo=True)
            kwargs.setdefault("workdir", "%s/work" %(rw_mount.path,))
        else:
            raise NotImplementedError("combined fs_type=%r is not implemented" % (kwargs["fs_type"]))
        self.mount("comnt-src", **kwargs)


class KVer(object):
    def __init__(self, s):
        self.value = [[int(y) if y.isdigit() else y for y in z.split(".")] for z in s.split("-")]

    def __str__(self):
        return "-".join([".".join(map(str, v)) for v in self.value])

    def __cmp__(self, other):
        if isinstance(other, KVer):
            other = other.value
        elif isinstance(other, (tuple, list)):
            pass
        else:
            other = KVer(other).value
        return cmp(self.value, other)


class SourceList(FSPath):
    base_url = None
    kernel_fn_re = re.compile(r'.*?(?P<arch>x86_64|i[3-6]86)/(?:[0-9][0-9]-)?kernel-(?P<kver>[0-9]+\.[0-9].*)\.sfs$')
    url_re = re.compile(r'^[a-z+]+://', re.I)

    @cached_property
    def run_env(self): # pylint: disable=method-hidden
        return {}

    def __iter__(self):
        src = self if self.exists else dl.dl_file(self.path)
        for line in src.open():
            words = line.strip().split()
            # comments or empty lines
            if not words or line.startswith("#"):
                continue

            # environment definition
            if '=' in words[0]:
                env_k, env_v = line.strip().split('=', 1)
                self.run_env[env_k] = env_v
                continue

            if len(words) < 2:  # source is exactly same string as target
                sfs_name, sfs_source_url = words[0], words[0]
            else:
                sfs_name, sfs_source_url = words[:2]
            # combine with base url if source url is not an URL
            if self.base_url and not self.url_re.match(sfs_source_url):
                sfs_source_url = os.path.join(self.base_url, sfs_source_url)

            # define "base" url
            if sfs_name == '*':
                self.base_url = None if sfs_source_url == '*' else sfs_source_url
                continue

            kernel_m = self.kernel_fn_re.match(sfs_name)
            if kernel_m:
                self.kernel_sfs = sfs_name
                self.arch = kernel_m.group("arch")
                self.kver = KVer(kernel_m.group('kver'))
            yield (sfs_source_url, sfs_name.lstrip('/'))
            self.run_env = {}


class BootDirBuilder(FSPath):
    dist_dirname = 'sfs'
    build_targets = set(os.environ.get("LBU_BUILD_TARGETS", "efi sfs ramdisk grubconf vmlinuz").split())
    mkrd_src_url = os.environ.get("LBU_MKRD_URL", "https://github.com/SFSLiveBoot/make-ramdisk.git")

    efi_mods = ["configfile", "ext2", "fat"'', "part_gpt", "part_msdos", "normal", "linux", "ls", "boot", "echo",
                "reboot", "search", "search_fs_file", "search_fs_uuid", "search_label", "help", "ntfs", "ntfscomp",
                "hfsplus", "chain", "multiboot", "terminal", "lspci", "font", "efi_gop", "efi_uga", "gfxterm"]
    efi_arch = "x86_64-efi"
    iso_output = None
    kernel_append = os.environ.get('BOOT_KERNEL_APPEND')
    serial_console = os.environ.get('BOOT_SERIAL_CONSOLE')

    LXC_DEST_ARCH = "/destdir/arch"
    LXC_MKRD_DIR = "/usr/src/make-ramdisk"
    LXC_DEST_BOOTDIR = "/destdir/bootdir"
    LXC_DEST_ISO_PARENT = "/destdir/iso-parent"

    mkrd_pkgs = os.environ.get("LBU_MKRD_PKGS", "make binutils udev klibc-utils lvm2 kmod cryptsetup-run busybox pciutils fakeroot cpio").split()
    grub_pkgs = os.environ.get("LBU_GRUB_PKGS", "grub2-common grub-efi-amd64-bin grub-pc-bin mtools xorriso").split()

    @cached_property
    def source_list_url(self):
        raise AttributeError("source_list_url property needs to be set")

    @cached_property
    def source_list(self):
        return SourceList(self.source_list_url)

    @cached_property
    def lxc_buildconf_d(self):
        d = MountPoint(os.path.join(lbu_cache_dir, "builder-lxc-conf-%d" % (os.getpid(),)), auto_remove=True)
        if not d.is_mounted:
            d.mount("mkrd-rw", fs_type="tmpfs", mode="0755")
        return d

    @cached_property
    def build_lxc(self):
        bind_dirs = []
        sfs_parts = SFSBuilder.default_lxc_parts+[self.lxc_buildconf_d]
        if 'ramdisk' in self.build_targets or 'ramdisk_net' in self.build_targets:
            mkrd_git_dir = dl.dl_file(self.mkrd_src_url)
            self.lxc_buildconf_d.join(self.LXC_MKRD_DIR).makedirs(sudo=True)
            bind_dirs.append(LXC.BindEntry(mkrd_git_dir, self.LXC_MKRD_DIR, True))
            sfs_parts.append(self.kernel_sfs.realpath())
        self.lxc_buildconf_d.join(self.LXC_DEST_BOOTDIR).makedirs(sudo=True)
        bind_dirs.append(LXC.BindEntry(self.realpath(), self.LXC_DEST_BOOTDIR))
        if "vmlinuz" in self.build_targets or "ramdisk" in self.build_targets or "ramdisk_net" in self.build_targets or "sfs" in self.build_targets:
            self.lxc_buildconf_d.join(self.LXC_DEST_ARCH).makedirs(sudo=True)
            bind_dirs.append(LXC.BindEntry(self.arch_dir.realpath(), self.LXC_DEST_ARCH))
        if self.iso_output:
            self.lxc_buildconf_d.join(self.LXC_DEST_ISO_PARENT).makedirs(sudo=True)
            bind_dirs.append(LXC.BindEntry(FSPath(self.iso_output).parent_directory.realpath(),
                                           self.LXC_DEST_ISO_PARENT))
        lxc = LXC.from_sfs("builddir-%d" % (os.getpid(),), auto_remove=True, sfs_parts=sfs_parts, bind_dirs=bind_dirs)
        return lxc

    @cached_property
    def kernel_sfs(self):
        return SFSFile(self.dist_dir.join(self.source_list.kernel_sfs))

    @cached_property
    def arch_dir(self):
        return self.dist_dir.join(self.arch)

    @cached_property
    def arch(self):
        return self.source_list.arch

    @cached_property
    def kver(self):
        return self.source_list.kver

    @cached_property
    def run_env(self):
        return self.source_list.run_env

    @cached_property
    def extra_dirs(self):
        ret = []
        for _, sfs_name in self.source_list:
            if not '/' in sfs_name:
                continue
            sfs_base = os.path.dirname(sfs_name)
            if sfs_base == self.source_list.arch:
                continue
            if sfs_base in ret:
                continue
            ret.append(sfs_base)
        return ret

    @cached_property
    def dist_dir(self):
        return SFSDirectory(self.join(self.dist_dirname))

    def build(self):
        if 'sfs' in self.build_targets:
            self.build_sfs()
        if 'vmlinuz' in self.build_targets:
            self.build_vmlinuz()
        if 'ramdisk' in self.build_targets:
            self.build_ramdisk()
        if 'ramdisk_net' in self.build_targets:
            self.build_ramdisk(NET='1')
        if 'efi' in self.build_targets:
            self.build_efi()
        if 'grubconf' in self.build_targets:
            self.build_grubconf()
        if self.iso_output:
            self.dist_dir.prune_old_sfs()
            lxc_iso = FSPath(self.LXC_DEST_ISO_PARENT).join(FSPath(self.iso_output).basename).path
            if self.grub_pkgs:
                self.build_lxc.apt_install(self.grub_pkgs)
            self.build_lxc.run(
                ["grub-mkrescue", "-o", lxc_iso, self.LXC_DEST_BOOTDIR], show_output=True)

    def build_sfs(self):
        info("Building sfs files to %s", self.dist_dirname)
        source_list = self.source_list
        for src_url, sfs_name in source_list:
            info("Building: %s -> %s", src_url, sfs_name)
            dest_sfs = SFSFile(self.dist_dir.join(sfs_name.lstrip('/')))
            dest_sfs.parent_directory.makedirs()
            if os.path.isfile(src_url) or (
                    (src_url.endswith('.sfs') and (src_url.startswith('http://') or src_url.startswith('https://')))):
                src_sfs = SFSFile(src_url)
                if not dest_sfs.exists or src_sfs.create_stamp > dest_sfs.create_stamp:
                    dest_sfs.replace_with(src_sfs, pr_cls(src_sfs.file_size))
                sfs_finder.register_sfs(dest_sfs)
                continue
            if dest_sfs.exists and not dest_sfs.needs_update:
                info("No change: %s is up to date (%s)", sfs_name, stamp2txt(dest_sfs.create_stamp))
                sfs_finder.register_sfs(dest_sfs)
                continue
            dest_sfs.rebuild_and_replace(src_url, env=source_list.run_env)

    def build_vmlinuz(self):
        info("Extracting vmlinuz-%s", self.kver)
        with self.arch_dir.open_file("vmlinuz-%s" % (self.kver), "wb") as vmlnz:
            vmlnz.write(self.kernel_sfs.open_file("boot/vmlinuz-%s" % (self.kver)).read())

    def build_grubconf(self):
        info("Creating grub config")
        self.join("boot/grub").open_file("grub.cfg", "w").write(
            open(os.path.join(lbu_dir, "scripts", "grub.cfg")).read())

        with self.join("grubvars.cfg").open("w") as gcfg:
            gcfg.write('set dist="%s"\n' % (self.dist_dirname,))
            gcfg.write('set kver="%s"\n' % (self.kver,))
            gcfg.write('set arch="%s"\n' % (self.arch_dir.basename,))
            if self.extra_dirs:
                gcfg.write('set extras="%s"\n' % (" ".join(self.extra_dirs)))
            if self.kernel_append:
                gcfg.write('set append="%s"\n' % (self.kernel_append,))
            if self.serial_console:
                gcfg.write('set ser_cons="%s"\n' % (self.serial_console,))

    def build_efi(self):
        info("Building EFI image (%s)", self.efi_arch)
        efi_img = "EFI/Boot/bootx64.efi"
        grub_prefix = "/boot/grub"
        self.join(efi_img).parent_directory.makedirs()
        self.join(grub_prefix).makedirs()
        lxc_efi_img = "%s/%s" % (self.LXC_DEST_BOOTDIR, efi_img)
        lxc_grub_dir = "%s%s" % (self.LXC_DEST_BOOTDIR, grub_prefix)
        lxc_efi_src = "/usr/lib/grub/%s" % (self.efi_arch,)

        if self.grub_pkgs:
            self.build_lxc.apt_install(self.grub_pkgs)
        self.build_lxc.run(["cp", "-r", lxc_efi_src, lxc_grub_dir])
        self.build_lxc.run(["grub-mkimage", "-o", lxc_efi_img, "-O", self.efi_arch, "-p", grub_prefix] + self.efi_mods)

    def build_ramdisk(self, **makeargs):
        info("Building ramdisk-%s", self.kver)
        if "KVERS" not in makeargs:
            makeargs["KVERS"] = self.kver
        if "RAMDISK_DESTDIR" not in makeargs:
            makeargs["RAMDISK_DESTDIR"] = "%s/" % (self.LXC_DEST_ARCH,)
        self.arch_dir.makedirs()
        if self.mkrd_pkgs:
            self.build_lxc.apt_install(self.mkrd_pkgs)
        cmd = ["make", "-C", self.LXC_MKRD_DIR] + \
            ["%s=%s" % (k, makeargs[k]) for k in makeargs]
        self.build_lxc.run(cmd, env=self.run_env, show_output=True)


@cli_func(desc="List AUFS/OverlayFS original components")
def list_components(directory='/'):
    fn_ts_re = re.compile(r'^(.+)\.([0-9]+)$')
    ret = []
    mntpnt=MountPoint(directory)
    for c in mntpnt.component_files:
        c_mnt=c.mountpoint
        try: lbe=c_mnt.loop_backend
        except NotLoopDev:
            ret.append("%s/"%c)
            continue
        m = fn_ts_re.match(lbe)
        if m:
            lbe_bn = m.group(1)
            if os.path.exists(lbe_bn) and os.path.samefile(lbe_bn, lbe):
                lbe = lbe_bn
        ret.append(lbe)
    return ret


@cli_func(desc="Get basic info about SFS file")
def sfs_info(filename):
    sfs = SFSFile(filename)
    ret = dict(basename=dict(stripped=sfs.basename.strip_down(), priority=sfs.basename.prio()),
               realpath=sfs.realpath().path,
               create_stamp=stamp2txt(sfs.create_stamp),
               size=sfs.file_size,
               git_source=sfs.git_source,
               git_commit=sfs.git_commit,
               git_branch=sfs.git_branch,
               curlink_sfs=sfs.curlink_sfs().path)
    for k, v in list(ret.items()):
        if v is None:
            del ret[k]
    return ret


@cli_func(desc="Find out the primary SFS file")
def get_root_sfs():
    test_file=FSPath("/bin/true")
    root_backend=SFSFile(test_file.backend)
    if not root_backend.validate_sfs(): return RuntimeError("Not running under SFS-based system?")
    return root_backend


def run_as_root(*args):
    if os.getuid(): os.execvp("sudo", ["sudo"] + list(args))


def mountpoint_x(dev):
    dev=os.stat(dev).st_rdev
    if not dev: return None
    return "%d:%d"%(dev>>8, dev&0xff)


def blkid2mnt(blkid):
    with open("/proc/mounts") as proc_mounts:
        for a, b, _ in [line.split(None, 2) for line in proc_mounts]:
            try:
                if mountpoint_x(a)==blkid:
                    return b.replace("\\040", " ")
            except OSError: pass
    raise FilesystemError("No mountpoint for block device %r"%blkid)


def _root_command_out(cmd):
    return run_command(cmd)


def _read_until_block(fobj):
    old_flags=fcntl.fcntl(fobj, fcntl.F_GETFL)
    fcntl.fcntl(fobj, fcntl.F_SETFL, old_flags|os.O_NONBLOCK)
    buf=[]
    while True:
        try: d=fobj.read(1)
        except IOError as e:
            if e.errno == errno.EWOULDBLOCK:
                break
            else:
                raise
        if not d: break
        buf.append(d)
    fcntl.fcntl(fobj, fcntl.F_SETFL, old_flags)
    return ''.join(buf) if isinstance(d, str) else b''.join(buf)


def run_command(cmd, cwd=None, show_output=False, env={}, as_user=None):
    if as_user is not None:
        if isinstance(as_user, int):
            as_user = pwd.getpwuid(as_user)
        elif isinstance(as_user, str):
            as_user = pwd.getpwnam(as_user)

        if not os.getuid() == as_user.pw_uid:
            info("Change user %d->%d for %r", os.getuid(), as_user.pw_uid, cmd)
            cmd = ['sudo', '-E', '-u', as_user.pw_name] + cmd

    cmd_env = {"PATH": "/sbin:/usr/sbin:" + os.environ["PATH"]}
    for k, v in env.items():
        if v is None:
            if k in cmd_env: del cmd_env[k]
        else:
            cmd_env[k] = v

    debug("Running: %r", cmd)

    proc=subprocess.Popen(cmd, env=cmd_env,
                          cwd=cwd, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    stdout_buf=[]
    stderr_buf=[]
    check_f = [proc.stdout, proc.stderr]
    while check_f:
        in_f = select.select(check_f, [], [], 1)[0]
        if not in_f:
            continue
        for buf, f, log_tag, sys_f in ((stdout_buf, proc.stdout, 'stdout', sys.stdout),
                                       (stderr_buf, proc.stderr, 'stderr', sys.stderr)):
            if f not in in_f: continue
            data=_read_until_block(f)
            if not data:
                check_f.remove(f)
                continue
            buf.append(data)
            if show_output:
                sys_f.write(data.decode('utf8').replace("\r\n", "\n").replace("\n", "\r\n"))
                sys_f.flush()
            debug("%s: %r", log_tag, data.rstrip(b'\n'))
    stderr_data = b"".join(stderr_buf).rstrip(b'\n').decode("utf8")
    stdout_data = b"".join(stdout_buf).rstrip(b'\n').decode("utf8")
    rcode = proc.wait()
    if rcode:
        raise CommandFailed(cmd, rcode, stderr_data, stdout_data)
    return stdout_data


@cli_func(desc="Show single blkid(8) tag value for specified device")
def blkid_value(blk_dev, name):
    return _root_command_out(["blkid", "-o", "value", "-s", name, blk_dev])


def mount(src, dst, *opts, **kwargs):
    cmd=["mount", src, dst]
    if kwargs.pop("bind", False):
        cmd.append("--bind")
    if opts or kwargs:
        cmd.extend(["-o", ",".join(list(opts) + ["%s=%s"%(k, kwargs[k]) for k in kwargs])])
    run_command(cmd, as_user='root')


@cli_func(parse_argv=lambda argv: ((argv[0],), {"pos": int(argv[1])} if len(argv)>1 else {}),
          desc="Find device (or other field position in mtab) for a mountpoint")
def mnt2dev(mnt, pos=0):
    esc_name=os.path.realpath(mnt).replace(" ", "\\040")
    with open("/proc/mounts") as proc_mounts:
        return list(filter(lambda l: l[1]==esc_name, [line.strip().split() for line in proc_mounts]))[-1][pos]


@cli_func(desc="Find disk name holding specified partition")
def part2disk(dev):
    dev=dev.split("/")[-1]
    for d in glob.glob("/sys/block/*"):
        if os.path.exists(os.path.join(d, dev, "partition")):
            return d.split("/")[-1]
    raise FilesystemError("No partition for device %r"%(dev,))


@cli_func(desc="Find a block device based on blkid(8) tag")
def blkid_find(**tags):
    cmd=reduce(lambda a, b: a + ["-t", "%s=%s"%b], list(tags.items()), ["blkid", "-o", "device"])
    return _root_command_out(cmd).split("\n")

_url_re=re.compile(r'^(?P<schema>https?|ftp)://.*')


@cli_func(desc="Show file or URL-based SFS file create stamp")
def sfs_stamp(src):
    if _url_re.match(src):
        with urllib.request.urlopen(src) as file_obj:
            return sfs_stamp_file(file_obj)
    else: return sfs_stamp_file(src)


@cli_func(desc="Rebuild a SFS file. Recognizes {PRE_,LAST_,}BUILD_SCRIPT vars.")
def rebuild_sfs(target, source=None, *env_vars):
    sfs = SFSFile(target)
    if source=="": source=None
    env = {}
    for kv in env_vars:
        k, v = kv.split("=", 1)
        env[k] = v
    sfs.rebuild_and_replace(source, env=env)


def _sfs_nfo_func(fname):
    try: st=os.stat(fname)
    except OSError as e:
        warn("Failed reading file info: %s", e)
        return
    ret=dict(size=st.st_size,
             mtime=datetime.datetime.fromtimestamp(st.st_mtime, UTC()).isoformat())
    sfs = SFSFile(fname)
    if sfs.validate_sfs():
        dt=datetime.datetime.fromtimestamp(sfs.create_stamp, UTC())
        ret["mtime"]=dt.isoformat()
    return ret

def _sfs_list_rm_empty(node):
    if "files" in node and not node["files"]: del node["files"]
    if "dirs" in node:
        for n in list(node["dirs"].keys()):
            _sfs_list_rm_empty(node["dirs"][n])
            if not node["dirs"][n]: del node["dirs"][n]
        if not node["dirs"]: del node["dirs"]

@cli_func(desc="Generate matching file tree of target dir")
def gen_sfs_list(target_dir, exclude_pat="", include_pat="*.sfs,*/vmlinuz-*,*/ramdisk*"):
    return FSPath(target_dir, walk_pattern=include_pat, walk_exclude=exclude_pat).file_tree

@cli_func(desc="Retrieve sfs creation stamp from file-like object")
def sfs_stamp_file(f):
    close=False
    if isinstance(f, str):
        close=True
        f=open(f, "rb")
    try: d=f.read(1024)
    finally:
        if close: f.close()
    if d[:4] != b"hsqs":
        raise NotSFS("file does not have sqsh signature")
    return struct.unpack("<I", d[8:8 + 4])[0]

def _update_sfs_parse_args(argv):
    if argv[0] == '--no-act':
        return [argv[1], True] + argv[2:], {}
    else:
        return [argv[0], False] + argv[1:], {}


@cli_func(parse_argv=_update_sfs_parse_args,
          desc="Update (or list only) a SFS collection (by defaults components of '/')")
def update_sfs(source_dir, no_act=False, *target_dirs):
    """[--no-act] {--list | --auto-rebuild | <source_dir>} [<target_dirs>...]"""
    if not source_dir[:2] == '--':
        source_dir = SFSDirectory(source_dir)
    target_dirs=list(map(SFSDirectory, target_dirs))
    if not target_dirs: target_dirs=(SFSDirectoryAufs(), )
    skip_sfs = set(os.environ.get("SFS_UPDATE_SKIP", "").split(","))
    for target_dir in target_dirs:
        last_dir=None
        target_dir_all_sfs = target_dir.all_sfs
        # make sure more basic lower-level SFS files (like 00-*) get rebuilt first
        if isinstance(target_dir, SFSDirectoryAufs):
            target_dir_all_sfs = reversed(target_dir_all_sfs)
        for sfs in target_dir_all_sfs:
            if not sfs.parent_directory == last_dir:
                last_dir=sfs.parent_directory
                info("Processing directory: %s", last_dir)
            try:
                if "/" in sfs.symlink_target:
                    info("Skipping non-local symlink: %s -> %s", sfs.basename, sfs.symlink_target)
                    continue
            except OSError: pass
            if sfs.basename.strip_down() in skip_sfs:
                info("Skipping ('%s' listed in $SFS_UPDATE_SKIP)", sfs.basename.strip_down())
                continue
            if source_dir=='--list':
                print(sfs.path)
                continue
            dst_sfs = sfs.curlink_sfs()
            cksum_file = os.environ.get("SFS_CHECKSUM_FILE", None)
            if cksum_file=="": pass
            elif cksum_file is None:
                cksum_file = ChecksumFile.find_in_parents_of(dst_sfs)
            else: cksum_file = ChecksumFile(cksum_file)
            if cksum_file:
                dst_sfs.checksum_file=cksum_file
            if source_dir=='--auto-rebuild':
                if dst_sfs.git_source:
                    info("Git repo for %s: %s", dst_sfs.basename, dst_sfs.git_source)
                if dst_sfs.latest_stamp > dst_sfs.create_stamp:
                    info("Rebuilding %s: %s > %s", dst_sfs.basename,
                         stamp2txt(dst_sfs.latest_stamp), stamp2txt(dst_sfs.create_stamp))
                    if not no_act:
                        dst_sfs.rebuild_and_replace()
                else:
                    info("Keeping %s: latest %s %s current: %s", dst_sfs.basename,
                         stamp2txt(dst_sfs.latest_stamp),
                         "<" if dst_sfs.latest_stamp < dst_sfs.create_stamp else "=",
                         stamp2txt(dst_sfs.create_stamp))
                continue
            src_sfs=source_dir.find_sfs(dst_sfs.basename)
            if src_sfs is None:
                warn("Not found from update source, skipping: %s", dst_sfs.basename)
            elif src_sfs.create_stamp > dst_sfs.create_stamp:
                info("Replacing %s from %s: %s > %s", dst_sfs.basename, src_sfs.parent_directory,
                     stamp2txt(src_sfs.create_stamp), stamp2txt(dst_sfs.create_stamp))
                if not no_act:
                    dst_sfs.replace_with(src_sfs, progress_cb=pr_cls(src_sfs.file_size))
            elif src_sfs.create_stamp == dst_sfs.create_stamp:
                info("Keeping same %s: %s", dst_sfs.basename, stamp2txt(src_sfs.create_stamp))
            else:
                warn("Keeping newer %s: %s < %s",
                     dst_sfs.basename, stamp2txt(src_sfs.create_stamp), stamp2txt(dst_sfs.create_stamp))


@cli_func(desc="Build SFS directory from sources")
def build_sfs_dir(dest_dir, source_list, source_url=None):
    sources = SourceList(source_list, source_url=source_url)
    for sfs_source_url, sfs_name in sources:
        dest_sfs = SFSFile(path=os.path.join(dest_dir, sfs_name.lstrip('/')))
        if not os.path.exists(dest_sfs.parent_directory.path):
            os.makedirs(dest_sfs.parent_directory.path, 0o755)
        if dest_sfs.exists and not dest_sfs.needs_update:
            info("No change: %s is up to date (%s)", sfs_name, stamp2txt(dest_sfs.create_stamp))
            continue
        dest_sfs.rebuild_and_replace(sfs_source_url, env=sources.run_env)


@cli_func(desc="Download file to cache and return filename")
def dl_file(source, fname=None, cache_dir=None):
    return dl.dl_file(source, fname, cache_dir)


@cli_func(desc="Build a bootable directory")
def build_boot_dir(path, source_list, dist_name="sfs", iso_output=None):
    builder = BootDirBuilder(path, source_list_url=source_list, dist_dirname=dist_name, iso_output=iso_output)
    builder.build()

@cli_func(desc="Build ramdisk")
def build_ramdisk(dest_dir, kver, arch, kernel_sfs, *makerd_args):
    makeargs = {}
    for opt in makerd_args:
        k, v = opt.split('=', 1)
        makeargs[k] = v
    builder = BootDirBuilder(dest_dir, arch=arch, kver=kver, kernel_sfs=SFSFile(kernel_sfs), run_env={})
    builder.build_ramdisk(**makeargs)


@cli_func(desc="Locate specified SFS files")
def locate_sfs(*names):
    ret = []
    for name in names:
        try: ret.append(sfs_finder[name])
        except KeyError: pass
    return ret

@cli_func(desc="Mount combined filesystem from different parts")
def mount_combined(dest_dir, parts, fs_type=MountPoint.default_combined_fs_type):
    MountPoint(dest_dir).mount_combined(parts.split(), fs_type=fs_type)


@cli_func(desc="Update aufs branch real-time")
def aufs_update_branch(mnt, aufs="/"):
    mnt = MountPoint(mnt.rstrip('/'))
    sfs = SFSFile(mnt.loop_backend)
    cur_sfs = sfs.curlink_sfs()
    if cur_sfs.realpath() == sfs.realpath():
        info("Already up to date: %s", sfs)
        return
    info("Updating: %s -> %s", sfs, cur_sfs)
    aufs_mnt = MountPoint(aufs)
    comp_match = [c for c in aufs_mnt.aufs_components if c == mnt]
    if not comp_match:
        warn("Could not find component path %r in aufs mount %r", mnt.path, aufs_mnt.path)
        return
    cur_mnt = cur_sfs.mounted_path
    if cur_mnt is None:
        cur_mnt = cur_sfs.mount(auto_remove=False)
    cur_comp_match = [c for c in aufs_mnt.aufs_components if c == cur_mnt]
    sfs_mnt = MountPoint(comp_match[0])
    if cur_comp_match:
        warn("Updated component already included in AUFS (old: %d, new: %d)",
             comp_match[0].aufs_index, cur_comp_match[0].aufs_index)
    else:
        aufs_mnt.mount("aufs", "remount", "ins:%d:%s=rr" % (comp_match[0].aufs_index, cur_mnt))
    aufs_mnt.mount("aufs", "remount", "del:%s" % (sfs_mnt,))
    sfs_mnt.umount()
    sfs_mnt.remove_on_delete()
    return cur_mnt


@cli_func(desc='Run LXC instance. bind is space-separated entries of <src_dir>=<dst_dir>[:ro]')
def lxc_run(name, init='exec bash -i >&0 2>&0', sfs_parts='00-* settings scripts', bind=None, vlan=None, veth=None, devs=None, nonet=False):
    args = dict(sfs_parts=sfs_parts.split(), auto_remove=True, init_cmd=['sh', '-c', init], nonet=nonet)
    if vlan is not None:
        args["vlan"] = [v.split(":") for v in vlan.split(" ")]
    if veth is not None:
        args["veth"] = [v.split(":") for v in veth.split(" ")]
    if bind is not None:
        args["bind_dirs"] = []
        for b in bind.split():
            src, dst = b.split('=', 1)
            if dst.endswith(':ro'):
                dst, ro = dst[:-3], True
            else:
                ro = False
            args["bind_dirs"].append(LXC.BindEntry(src, dst, ro))
    if devs is not None:
        args["devices_allow"] = devs.split(",")
    lxc = LXC.from_sfs(name, **args)
    lxc.start(foreground=True)


@cli_func(desc="Build EFI-bootable directory (should be topdir of a removable)")
def build_efi(path, arch=os.uname()[4], kver=os.uname()[2]):
    builder = BootDirBuilder(path, build_targets={'efi', 'grubconf'}, arch=arch, kver=kver, extra_dirs=[])
    builder.build()

@cli_func(desc="delete old .sfs files from directory")
def prune_old_sfs(path):
    sfs_dir = SFSDirectory(path)
    sfs_dir.prune_old_sfs()

@cli_func(desc="locate original path for file in combined fs")
def locate_orig(path):
    f = FSPath(path)
    return [f1.path for f1 in f.component_files]
