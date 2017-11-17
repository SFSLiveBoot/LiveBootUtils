#!/usr/bin/python

from logging import info, warn, error
import logging
import subprocess
from lbu_common import SFSDirectory, get_root_sfs, CLIProgressReporter, stamp2txt, cli_func,\
    SFSDirectoryAufs, BadArgumentsError

logging.getLogger().setLevel(logging.INFO)

_log_colors=dict(map(lambda (k,v):  (k, "\033[%sm"%(v,)),dict(blue="34", red="1;31", yellow="33", reset="1;0").items()))
try: subprocess.check_call(["tty","-s"])
except subprocess.CalledProcessError: pass
else:
    logging.addLevelName(logging.INFO, "{blue}{level}{reset}".format(level=logging.getLevelName(logging.INFO), **_log_colors))
    logging.addLevelName(logging.WARNING, "{yellow}{level}{reset}".format(level=logging.getLevelName(logging.WARNING), **_log_colors))
    logging.addLevelName(logging.ERROR, "{red}{level}{reset}".format(level=logging.getLevelName(logging.ERROR), **_log_colors))


def update_sfs_parse_args(argv):
    if argv[0] == '--no-act':
        return [argv[1], True] + argv[2:], {}
    else:
        return [argv[0], False] + argv[1:], {}


@cli_func(parse_argv=update_sfs_parse_args,
          desc="Update (or list only) a SFS collection (by defaults components of '/')")
def update_sfs(source_dir, no_act=False, *target_dirs):
    """[--no-act] {--list | --auto-rebuild | <source_dir>} [<target_dirs>...]"""
    if not source_dir[:2] == '--':
        source_dir = SFSDirectory(source_dir)
    target_dirs=map(SFSDirectory, target_dirs)
    if not target_dirs: target_dirs=(SFSDirectoryAufs(), )
    for target_dir in target_dirs:
        last_dir=None
        for sfs in target_dir.all_sfs:
            if not sfs.parent_directory == last_dir:
                last_dir=sfs.parent_directory
                info("Processing directory: %s", last_dir)
            try:
                if "/" in sfs.symlink_target:
                    info("Skipping non-local symlink: %s -> %s", sfs.basename, sfs.symlink_target)
                    continue
            except OSError: pass
            if source_dir=='--list':
                print sfs.path
                continue
            dst_sfs = sfs.curlink_sfs()
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
                    dst_sfs.replace_with(src_sfs, progress_cb=CLIProgressReporter(src_sfs.file_size))
            elif src_sfs.create_stamp == dst_sfs.create_stamp:
                info("Keeping same %s: %s", dst_sfs.basename, stamp2txt(src_sfs.create_stamp))
            else:
                warn("Keeping newer %s: %s < %s",
                     dst_sfs.basename, stamp2txt(src_sfs.create_stamp), stamp2txt(dst_sfs.create_stamp))


if __name__ == '__main__':
    import sys, os
    args = sys.argv[:]
    arg0=os.path.basename(args.pop(0))
    try: command=args.pop(0)
    except IndexError:
        warn("Usage: %s [--debug] <command> [<args..>]", arg0)
        info("Supported commands:%s",
             "".join(map(lambda (n, f): "\n\t%s\t%s"%(n, getattr(f, "_cli_desc", "")),
                         sorted(cli_func.commands.iteritems()))))
        raise SystemExit(1)
    if command=='--debug':
        logging.getLogger().setLevel(logging.DEBUG)
        command=args.pop(0)
    logging.getLogger().name=command
    try: cmd_func=cli_func.commands[command]
    except KeyError:
        error("Unknown command: %s", command)
        raise SystemExit(1)
    try: ret=cmd_func.cli_call(args)
    except BadArgumentsError as e:
        error("Execution error: %s", e)
        info("Usage: %s %s %s", arg0, command, cmd_func.__doc__)
        raise SystemExit(1)
    if ret is not None:
        if isinstance(ret, list):
            for e in ret: print e
        elif isinstance(ret, dict):
            print __import__("json").dumps(ret, indent=True)
        else:
            print ret
