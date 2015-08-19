#!/usr/bin/python

import os, sys
import struct, time, functools
import fnmatch, glob, re
import subprocess
import urllib2

from logging import warn


class CommandFailed(EnvironmentError): pass


class FilesystemError(LookupError): pass


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


def cli_func(func=None, name=None):
    if func is None:
        def gen(func_real):
            if name is not None: func_real._cli_name=name
            return cli_func(func_real)
        return gen
    cli_func.commands[getattr(func, "_cli_name", func.__name__.replace("_", "-"))]=func
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


@cli_func(name="help")
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


class SFSFile(FSPath):
    progress_cb=None
    chunk_size=8192

    class SFSBasename(str):
        def strip_down(self):
            ret=self[3:] if fnmatch.fnmatch(self, "[0-9][0-9]-*") else self[:]
            try: ret=ret[:ret.rindex(".sfs")]
            except ValueError: pass
            return ret

        def __eq__(self, other):
            if super(SFSFile.SFSBasename, self) == other: return True
            return self.strip_down()==SFSFile.SFSBasename(other).strip_down()

    def validate_sfs(self):
        if not os.path.isfile(self.path): return False
        return self.open().read(4)=="hsqs"

    @cached_property
    def sfs_directory(self):
        if not self.validate_sfs(): raise ValueError("Not a SFS file", self.path)
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
        if not self.fs_type=="aufs": raise ValueError("Mountpoint is not aufs", self.path)
        components=[]
        glob_prefix="/sys/fs/aufs/si_%s/br"%(self.aufs_si,)
        for branch_file in glob.glob(glob_prefix + "[0-9]*"):
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
        if not loop_name.startswith("loop"): raise ValueError("Mountpoint does not seem to be loop device", loop_name)
        return open(os.path.join("/sys/block", loop_name, "loop/backing_file")).read().rstrip("\n")


@cli_func
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
    proc=subprocess.Popen(cmd, env={"PATH": "/sbin:/usr/sbin:" + os.environ["PATH"]},
                          stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    out=[]
    while True:
        data=proc.stdout.read()
        if not data: break
        out.append(data)
    if proc.wait():
        err=proc.stderr.read().strip()
        raise CommandFailed(err, "".join(out).rstrip("\n"))
    return "".join(out).rstrip("\n")


@cli_func
def blkid_value(blk_dev, name):
    return _root_command_out(["blkid", "-o", "value", "-s", name, blk_dev])


def mount(src, dst, *opts, **kwargs):
    cmd=["mount", src, dst]
    if kwargs.pop("bind", False):
        cmd.append("--bind")
    if opts or kwargs:
        cmd.extend(["-o", ",".join(list(opts) + map(lambda k: "%s=%s"%(k, kwargs[k]), kwargs))])
    _root_command_out(cmd)


@cli_func
def mnt2dev(mnt, pos=0):
    esc_name=os.path.realpath(mnt).replace(" ", "\\040")
    with open("/proc/mounts") as proc_mounts:
        return filter(lambda l: l[1]==esc_name, map(lambda line: line.strip().split(), proc_mounts))[-1][pos]


@cli_func
def part2disk(dev):
    dev=dev.split("/")[-1]
    for d in glob.glob("/sys/block/*"):
        if os.path.exists(os.path.join(d, dev, "partition")):
            return d.split("/")[-1]
    raise FilesystemError("No partition for device %r"%(dev,))


@cli_func
def blkid_find(**tags):
    cmd=reduce(lambda a, b: a + ["-t", "%s=%s"%b], tags.items(), ["blkid", "-o", "device"])
    return _root_command_out(cmd).split("\n")

_url_re=re.compile(r'^(?P<schema>https?|ftp)://.*')


@cli_func
def sfs_stamp(src):
    if _url_re.match(src):
        with urllib2.urlopen(src) as file_obj:
            return sfs_stamp_file(file_obj)
    else: return sfs_stamp_file(src)


@cli_func
def sfs_stamp_file(f):
    close=False
    if isinstance(f, basestring):
        close=True
        f=open(f, "rb")
    try: d=f.read(1024)
    finally:
        if close: f.close()
    if d[:4]!="hsqs": raise ValueError("file does not have sqsh signature")
    return struct.unpack("<I", d[8:8 + 4])[0]
