#!/usr/bin/python

import os, sys
import struct, time, functools
import fnmatch, glob, re
import fcntl, errno, select
import subprocess
import urllib2
import datetime
import pwd

from Crypto.Hash import MD5
from logging import warn, info, debug


class CommandFailed(EnvironmentError): pass


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
    for prop in filter(lambda p: isinstance(p, property) and hasattr(obj, "__cached__" + p.fget.__name__),
                       map(lambda n: getattr(obj.__class__, n), dir(obj.__class__))):
        delattr(obj, prop.fget.__name__)


def repr_wrap(fn):
    @functools.wraps(fn)
    def repr_gen(self):
        return "<%s.%s %r @%x>"%(self.__class__.__module__, self.__class__.__name__, fn(self), id(self))
    repr_gen._repr=fn
    return repr_gen


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
        func._cli_parse_argv=lambda argv: (argv, {})

    def cli_call(argv):
        try: args, kwargs=func._cli_parse_argv(argv)
        except Exception as e:
            raise BadArgumentsError("bad arguments: %s"%e)
        try: return func(*args, **kwargs)
        except TypeError as e:
            if e.message.startswith('%s() '%(func.__name__,)):
                raise BadArgumentsError(e)
            else: raise
    func.cli_call=cli_call
    if not func.__doc__:
        import inspect
        spec=inspect.getargspec(func)
        rev_args=list(reversed(spec.args))
        defaults=dict(map(lambda (i, d): (rev_args[i], d), enumerate(reversed(spec.defaults)))) if spec.defaults else {}
        func.__doc__=" ".join(map(lambda n: "[<%s>=%r]"%(n, defaults[n]) if n in defaults else "<%s>"%n, spec.args)+
                              (["[<%s>...]"%spec.varargs] if spec.varargs else [])+
                              (["[<%s>=<value>...]"%spec.keywords] if spec.keywords else []))
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
        self.report_buckets=map(lambda i: i * full_size / self.nr_buckets, range(self.nr_buckets))
        map(lambda k: setattr(self, k, attrs[k]), attrs)

    def __call__(self, sz):
        if sz is None: print >>self.output_stream, "100%"
        elif self.report_buckets and sz>=self.report_buckets[0]:
            print >>self.output_stream, "%d%%.."%(100 * sz / self.full_size),
            while self.report_buckets and sz>=self.report_buckets[0]:
                self.report_buckets.pop(0)
        self.output_stream.flush()


class SFSDirectory(object):
    @repr_wrap
    def __repr__(self):
        return str(self.backend)

    def __init__(self, backend):
        if isinstance(backend, basestring) and os.path.isdir(backend):
            backend=FSPath(backend)
        if isinstance(backend, FSPath):
            self.backend = backend
        else:
            raise ValueError("Unknown backend: (%s) %r" % (type(backend).__name__, backend))

    @cached_property
    def all_sfs(self):
        return list(self.backend.walk("*.sfs", file_class=SFSFile))

    def find_sfs(self, name):
        for sfs in self.all_sfs:
            if sfs.basename==name:
                return sfs


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

    def __init__(self, path, **attrs):
        if isinstance(path, FSPath): path=path.path
        if not isinstance(path, basestring):
            raise ValueError("Invalid init path type for %s: %s"%(self.__class__.__name__, type(path).__name__))
        self.path=path
        map(lambda k: setattr(self, k, attrs[k]), attrs)

    @cached_property
    def basename(self): return os.path.basename(self.path)

    @repr_wrap
    def __repr__(self): return self.path

    @cached_property
    def backend(self):
        if self.mountpoint.fs_type=="aufs":
            for mpt in map(lambda c: c.mountpoint, reversed(self.mountpoint.aufs_components)):
                if self.path in mpt: return FSPath(mpt.loop_backend)
        raise RuntimeError("Cannot determine backend of file", self.path)

    @cached_property
    def parent_directory(self):
        return FSPath(self._parent_path)

    def __eq__(self, other):
        if isinstance(other, FSPath):
            return self.path == other.path
        else:
            return super(FSPath, self) == other

    def __str__(self): return self.path

    def walk(self, pattern="*", file_class=None):
        if file_class is None: file_class=FSPath
        for d, dn, fn in os.walk(self.path):
            if not self.walk_hidden:
                dn[:]=filter(lambda x: not x.startswith("."), dn)
                fn[:]=filter(lambda x: not x.startswith("."), fn)
            for f in filter(lambda n: fnmatch.fnmatch(n, pattern), fn):
                yield file_class(os.path.join(d, f))

    @cached_property
    def file_size(self): return os.stat(self.path).st_size

    @cached_property
    def mountpoint(self):
        orig_dev=os.stat(self.path).st_dev
        path_components=os.path.realpath(self.path).split(os.path.sep)
        sub_paths=map(lambda n: os.path.sep.join(path_components[:n+1]) or os.path.sep, range(len(path_components)))
        cur_path=self.path
        for test_path in reversed(sub_paths):
            if not os.stat(test_path).st_dev==orig_dev:
                break
            cur_path=test_path
        return MountPoint(cur_path)

    @cached_property
    def _parent_path(self):
        parent_path=os.path.sep.join(os.path.realpath(self.path).rsplit(os.path.sep, 1)[:-1])
        if not parent_path: parent_path=os.path.sep
        return parent_path

    @property
    def symlink_target(self): return os.readlink(self.path)

    def replace_file(self, temp_filename, change_stamp=None, backup_name=None):
        is_link=os.path.islink(self.path)
        old_stat=os.stat(self.path)
        if is_link and change_stamp is None:
            change_stamp=os.stat(temp_filename).st_mtime
        if backup_name is None: backup_name="%s.OLD.%s"%(self.path, int(time.time()))
        os.rename(self.path, backup_name)
        if is_link:
            new_name="%s.%s"%(self.path, int(change_stamp))
            os.rename(temp_filename, new_name)
            os.symlink(os.path.basename(new_name), self.path)
        else:
            os.rename(temp_filename, self.path)
        try: os.chown(self.path, old_stat.st_uid, os.stat(self.path).st_gid)
        except OSError: pass
        try: os.chown(self.path, os.stat(self.path).st_uid, old_stat.st_gid)
        except OSError: pass
        try: os.chmod(self.path, old_stat.st_mode)
        except OSError as e:
            warn("Failed to change new file mode to %o: %s", old_stat.st_mode, e)
        clear_cached_properties(self)

    @property
    def loop_dev(self):
        for devname in os.listdir('/sys/block'):
            if not devname.startswith('loop'):
                continue
            try: bfile = open('/sys/block/%s/loop/backing_file'%(devname,)).read().strip()
            except IOError:
                continue
            if not os.path.exists(bfile):
                continue
            if not os.path.samefile(bfile, self.path):
                continue
            if not int(open('/sys/block/%s/loop/offset'%(devname,)).read().strip())==0:
                continue
            return '/dev/%s'%(devname,)


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
    def proc_mountinfo_line(line):
        parts=line.rstrip('\n').split(' ')
        ret = dict(mount_id=int(parts[0]), parent_id=int(parts[1]),
                   st_dev=reduce(lambda a, b: (a<<8)+b, map(int, parts[2].split(':'))),
                   root=parts[3].decode("string_escape"), mnt=parts[4].decode("string_escape"),
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
    def last_stamp(self):
        return int(run_command(['git', 'log', '-1', '--format=%ct'], cwd=self.path))


class Downloader(object):
    git_url_re = re.compile(r'(^git://.*?|^git\+.*?|.*?\.git)(?:#(?P<branch>.*))?$')

    @cached_property
    def cache_dir(self):
        cache_dir = os.path.expanduser("~/.cache/lbu/dl")
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, 0755)
        return cache_dir

    def dl_file(self, source, dest_dir=None, fname=None):
        if dest_dir is None:
            dest_dir = self.cache_dir
        if fname is None:
            fname = "%s-%s" % (MD5.new(source).hexdigest()[:8], os.path.basename(source))
            if fname.endswith('.git'):
                fname = fname[:-4]
        dest = os.path.join(dest_dir, fname)

        git_m = self.git_url_re.match(source)
        if git_m:
            git_branch = git_m.group('branch')
            if git_branch is not None:
                source = source[:git_m.start('branch') - 1]
                if dest.endswith('#%s' % (git_branch,)):
                    dest = dest[:-len(git_branch) - 1]
                dest = '%s@%s' % (dest, git_branch)

            if source[:4] == 'git+':
                source = source[4:]
            if os.path.exists(dest):
                cmd=['git', 'pull', '--recurse-submodules', source]
                if git_branch: cmd+=[git_branch]
                run_command(cmd, cwd=dest)
                if os.path.exists(os.path.join(dest, '.gitmodules')):
                    run_command(['git', 'submodule', 'update', '--depth', '1'], cwd=dest)
                return GitRepo(dest)
            else:
                cmd=['git', 'clone', '--recurse-submodules']
                if git_branch: cmd+=['-b', git_branch]
                cmd+=['--depth=1', source, dest]
                run_command(cmd)
                return GitRepo(dest)
        raise NotImplementedError('URL download', source)


dl = Downloader()


class SFSFile(FSPath):
    GIT_SOURCE_PATH='/usr/src/sfs.d/.git-source'
    GIT_COMMIT_PATH='/usr/src/sfs.d/.git-commit'
    UPTDCHECK_PATH='/usr/src/sfs.d/.check-up-to-date'
    PARTS_DIR='/.parts'

    progress_cb=None
    chunk_size=8192
    auto_unmount = False

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
                return 99

        def __eq__(self, other):
            if super(SFSFile.SFSBasename, self) == other: return True
            return self.strip_down()==SFSFile.SFSBasename(other).strip_down()

    def validate_sfs(self):
        if not os.path.isfile(self.path): return False
        return self.open().read(4)=="hsqs"

    fn_ts_re = re.compile(r'^(.+?)(?:(\.OLD)?\.([0-9]+))+$')

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

    def open(self, mode="rb"):
        return open(self.path, mode)

    @cached_property
    def mounted_path(self):
        ldev = self.loop_dev
        if ldev is None: return
        mentry = global_mountinfo.find_dev(ldev)
        if mentry is None: return
        return mentry["mnt"]

    @cached_property
    def git_source(self):
        try: git_source = self.open_file(self.GIT_SOURCE_PATH).read().strip()
        except IOError: return
        if '#' in git_source:
            git_source, self.git_branch = git_source.rsplit('#', 1)
        else:
            self.git_branch = None
        return git_source

    @cached_property
    def git_commit(self):
        try: return self.open_file(self.GIT_COMMIT_PATH).read().strip()
        except IOError: pass

    @cached_property
    def git_branch(self):
        if self.git_source is None: return

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
        except IOError as e:
            return self.create_stamp

        if self.mounted_path == None:
            self.mount()
            self.auto_unmount = True
        try:
            run_command(os.path.join(self.mounted_path, self.UPTDCHECK_PATH.lstrip('/')),
                        show_output=True, env=dict(DESTDIR=self.mounted_path))
        except CommandFailed as e:
            return int(time.time())
        return self.git_repo.last_stamp if self.git_source else self.create_stamp

    def open_file(self, path):
        if self.mounted_path == None:
            self.mount()
            self.auto_unmount = True
        return open(os.path.join(self.mounted_path, path.lstrip('/')), 'rb')

    def mount(self, mountdir=None):
        if mountdir is None:
            mountdir = os.path.join(self.PARTS_DIR, "%02d-%s.%d" % (
                self.basename.prio(), self.basename.strip_down(), self.create_stamp))
            if not os.path.exists(mountdir):
                run_command(['mkdir', '-p', mountdir], as_user='root')
        mount(self.path, mountdir, 'loop', 'ro')
        self.mounted_path = mountdir

    def rebuild_and_replace(self, source=None):
        cmd = [os.path.join(os.path.dirname(__file__), 'scripts/rebuild-sfs.sh', ), '--auto']
        if source is not None:
            cmd.append(source)
        cmd.append(self.path)
        run_command(cmd, as_user='root', show_output=True, env=dict(dl_cache_dir=dl.cache_dir))

    def replace_with(self, other, progress_cb=None):
        dst_temp="%s.NEW.%s"%(self.path, os.getpid())
        dst_fobj=open(dst_temp, "wb")
        create_stamp=None
        with other.open() as src_fobj:
            nbytes=0
            if progress_cb: progress_cb(nbytes)
            while True:
                data=src_fobj.read(self.chunk_size)
                if data=="": break
                if create_stamp is None:
                    create_stamp=self._get_create_stamp(data)
                nbytes+=len(data)
                dst_fobj.write(data)
                if progress_cb: progress_cb(nbytes)
            if progress_cb: progress_cb(None)
        dst_fobj.close()
        self.replace_file(dst_temp, create_stamp)

    def __del__(self):
        if self.auto_unmount:
            run_command(['umount', self.mounted_path], as_user='root')
            run_command(['rmdir', self.mounted_path], as_user='root')


_mount_tab=None


def _load_mount_tab():
    global _mount_tab
    _mount_tab=map(lambda l: l.rstrip("\n").split(), reversed(list(open("/proc/mounts"))))


class MountPoint(FSPath):
    @cached_property
    def fs_type(self): return self._find_mount_tab_entry().fs_type

    @cached_property
    def mount_source(self): return self._find_mount_tab_entry().mount_source

    def _find_mount_tab_entry(self):
        if _mount_tab is None: _load_mount_tab()
        for mount_tab_entry in _mount_tab:
            mount_tab_path=mount_tab_entry[1].replace("\\040", " ")
            if mount_tab_path == self.path:
                self.mount_source, path, self.fs_type, self.mount_options=mount_tab_entry[:4]
                return self
        raise RuntimeError("Cannot find mountpoint entry", self.path)

    @cached_property
    def aufs_si(self):
        return filter(lambda x: x.startswith("si="), self.mount_options.split(","))[0].split("=")[1]

    @cached_property
    def aufs_components(self):
        if not self.fs_type=="aufs": raise NotAufs("Mountpoint is not aufs", self.path)
        components=[]
        glob_prefix="/sys/fs/aufs/si_%s/br"%(self.aufs_si,)
        for branch_file in sorted(glob.glob(glob_prefix + "[0-9]*"), key=lambda v: int(v[len(glob_prefix):])):
            branch_dir, branch_mode=open(branch_file).read().strip().rsplit("=", 1)
            components.append(FSPath(branch_dir, aufs_mode=branch_mode, aufs_index=int(branch_file[len(glob_prefix):])))
        return components

    def __contains__(self, item):
        if isinstance(item, basestring):
            ret=os.path.exists(os.path.join(self.path, "./" + item))
            return ret
        raise ValueError("Unknown item type", type(item).__name__)

    @cached_property
    def loop_backend(self):
        loop_name=self.mount_source.split(os.path.sep)[-1]
        if not loop_name.startswith("loop"): raise NotLoopDev("Mountpoint does not seem to be loop device", loop_name)
        return open(os.path.join("/sys/block", loop_name, "loop/backing_file")).read().rstrip("\n")


@cli_func(desc="List AUFS original components")
def aufs_components(directory='/'):
    fn_ts_re = re.compile(r'^(.+)\.([0-9]+)$')
    ret = []
    mntpnt=MountPoint(directory)
    for c in mntpnt.aufs_components:
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
        for a, b, c in map(lambda line: line.split(None, 2), proc_mounts):  # @UnusedVariable
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
        if d=='': break
        buf.append(d)
    fcntl.fcntl(fobj, fcntl.F_SETFL, old_flags)
    return ''.join(buf)


def run_command(cmd, cwd=None, show_output=False, env={}, as_user=None):
    if as_user is not None:
        if isinstance(as_user, int):
            as_user = pwd.getpwuid(as_user)
        elif isinstance(as_user, basestring):
            as_user = pwd.getpwnam(as_user)

        if not os.getuid() == as_user.pw_uid:
            info("Change user %d->%d for %r", os.getuid(), as_user.pw_uid, cmd)
            cmd = ['sudo', '-E', '-u', as_user.pw_name] + cmd

    cmd_env = {"PATH": "/sbin:/usr/sbin:" + os.environ["PATH"]}
    for k, v in env.iteritems():
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
            if data=='':
                check_f.remove(f)
                continue
            buf.append(data)
            if show_output:
                sys_f.write(data)
                sys_f.flush()
            debug("%s: %r", log_tag, data.rstrip('\n'))
    stderr_data = "".join(stderr_buf).rstrip('\n')
    stdout_data = "".join(stdout_buf).rstrip('\n')
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
        cmd.extend(["-o", ",".join(list(opts) + map(lambda k: "%s=%s"%(k, kwargs[k]), kwargs))])
    run_command(cmd, as_user='root')


@cli_func(parse_argv=lambda argv: ((argv[0],), {"pos": int(argv[1])} if len(argv)>1 else {}),
          desc="Find device (or other field position in mtab) for a mountpoint")
def mnt2dev(mnt, pos=0):
    esc_name=os.path.realpath(mnt).replace(" ", "\\040")
    with open("/proc/mounts") as proc_mounts:
        return filter(lambda l: l[1]==esc_name, map(lambda line: line.strip().split(), proc_mounts))[-1][pos]


@cli_func(desc="Find disk name holding specified partition")
def part2disk(dev):
    dev=dev.split("/")[-1]
    for d in glob.glob("/sys/block/*"):
        if os.path.exists(os.path.join(d, dev, "partition")):
            return d.split("/")[-1]
    raise FilesystemError("No partition for device %r"%(dev,))


@cli_func(desc="Find a block device based on blkid(8) tag")
def blkid_find(**tags):
    cmd=reduce(lambda a, b: a + ["-t", "%s=%s"%b], tags.items(), ["blkid", "-o", "device"])
    return _root_command_out(cmd).split("\n")

_url_re=re.compile(r'^(?P<schema>https?|ftp)://.*')


@cli_func(desc="Show file or URL-based SFS file create stamp")
def sfs_stamp(src):
    if _url_re.match(src):
        with urllib2.urlopen(src) as file_obj:
            return sfs_stamp_file(file_obj)
    else: return sfs_stamp_file(src)


@cli_func(desc="Rebuild a SFS file, optionally from specified source")
def rebuild_sfs(target, source=None):
    sfs = SFSFile(target)
    sfs.rebuild_and_replace(source)


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
        for n in node["dirs"].keys():
            _sfs_list_rm_empty(node["dirs"][n])
            if not node["dirs"][n]: del node["dirs"][n]
        if not node["dirs"]: del node["dirs"]

@cli_func(desc="Generate list of files matching pattern in JSON format")
def gen_sfs_list(target_dir, exclude_pat="", include_pat="*.sfs,*/vmlinuz-*,*/ramdisk*"):
    ret={}
    exclude_pat=exclude_pat.split(",")
    include_pat=include_pat.split(",")
    orig_path=target_dir.split(os.path.sep)
    for d, dn, fn in os.walk(target_dir):
        dn[:]=filter(lambda n: not n.startswith("."), dn)
        path_parts=d.split(os.path.sep)[len(orig_path):]
        fn[:]=filter(lambda f: any(map(lambda pat: fnmatch.fnmatch(os.path.join(*(path_parts+[f])), pat), include_pat)), fn)
        fn[:]=filter(lambda f: not any(map(lambda pat: fnmatch.fnmatch(os.path.join(*(path_parts+[f])), pat), exclude_pat)), fn)
        dir_entry=reduce(lambda a, b: a["dirs"][b], path_parts, ret)
        dir_entry.setdefault("files", {}).update(filter(lambda x: x[1], map(lambda f: (f, _sfs_nfo_func(os.path.join(d, f))), fn)))
        dir_entry.setdefault("dirs", {}).update(map(lambda n: (n, {}), dn))
    _sfs_list_rm_empty(ret)
    return ret

@cli_func(desc="Retrieve sfs creation stamp from file-like object")
def sfs_stamp_file(f):
    close=False
    if isinstance(f, basestring):
        close=True
        f=open(f, "rb")
    try: d=f.read(1024)
    finally:
        if close: f.close()
    if d[:4]!="hsqs": raise NotSFS("file does not have sqsh signature")
    return struct.unpack("<I", d[8:8 + 4])[0]
