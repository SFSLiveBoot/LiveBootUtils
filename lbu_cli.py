#!/usr/bin/python

from logging import info, warn, error
import logging
import subprocess
from lbu_common import SFSDirectory, get_root_sfs, CLIProgressReporter, stamp2txt, cli_func

logging.getLogger().setLevel(logging.INFO)

_log_colors=dict(map(lambda (k,v):  (k, "\033[%sm"%(v,)),dict(blue="34", red="1;31", yellow="33", reset="1;0").items()))
try: subprocess.check_call(["tty","-s"])
except subprocess.CalledProcessError: pass
else:
    logging.addLevelName(logging.INFO, "{blue}{level}{reset}".format(level=logging.getLevelName(logging.INFO), **_log_colors))
    logging.addLevelName(logging.WARNING, "{yellow}{level}{reset}".format(level=logging.getLevelName(logging.WARNING), **_log_colors))
    logging.addLevelName(logging.ERROR, "{red}{level}{reset}".format(level=logging.getLevelName(logging.ERROR), **_log_colors))

@cli_func
def update_sfs(source_dir, *target_dirs):
    source_dir=SFSDirectory(source_dir)
    target_dirs=map(SFSDirectory, target_dirs)
    if not target_dirs: target_dirs=(get_root_sfs().sfs_directory, )
    for target_dir in target_dirs:
        last_dir=None
        for sfs in target_dir.all_sfs:
            if not sfs.parent_directory == last_dir:
                last_dir=sfs.parent_directory
                info("Processing directory: %s", last_dir)
            sfs_name=sfs.basename
            try:
                if "/" in sfs.symlink_target:
                    info("Skipping non-local symlink: %s -> %s", sfs_name, sfs.symlink_target)
                    continue
            except OSError: pass
            src_sfs=source_dir.find_sfs(sfs_name)
            if src_sfs is None:
                warn("Not found from update source, skipping: %s", sfs_name)
            elif src_sfs.create_stamp > sfs.create_stamp:
                info("Replacing %s from %s: %s > %s", sfs_name, src_sfs.parent_directory,
                     stamp2txt(src_sfs.create_stamp), stamp2txt(sfs.create_stamp))
                sfs.replace_with(src_sfs, progress_cb=CLIProgressReporter(src_sfs.file_size))
            elif sfs.create_stamp == sfs.create_stamp:
                info("Keeping same %s: %s", sfs_name, stamp2txt(src_sfs.create_stamp))
            else:
                warn("Keeping newer %s: %s < %s",
                     sfs_name, stamp2txt(src_sfs.create_stamp), stamp2txt(sfs.create_stamp))


if __name__ == '__main__':
    import sys, os
    arg0=os.path.basename(sys.argv[0])
    try: command=sys.argv[1]
    except IndexError:
        warn("Usage: %s <command> [<args..>]", arg0)
        info("Supported commands: %s", ", ".join(cli_func.commands.keys()))
        raise SystemExit(1)
    logging.getLogger().name=command
    try: cmd_func=cli_func.commands[command]
    except KeyError:
        error("Unknown command: %s", command)
        raise SystemExit(1)
    try: ret=cmd_func.cli_call(sys.argv[2:])
    except TypeError as e:
        error("Execution error: %s", e)
        info("Usage: %s %s %s", arg0, command, cmd_func.__doc__)
        raise SystemExit(1)
    if ret is not None:
        if isinstance(ret, list):
            for e in ret: print e
        else:
            print ret
