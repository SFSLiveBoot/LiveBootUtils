# Live Boot Utils (for SFSLiveBoot system)

Those scripts help to manage [SFSLiveBoot](https://github.com/SFSLiveBoot)-based linux distribution.

## Common operations

### Quick install (create new system .iso) from scratch

`curl https://raw.githubusercontent.com/SFSLiveBoot/LiveBootUtils/master/quick-build.sh | sh`

### (Re)building SFS component

- rebuild/update existing sfs, retaining settings (environment/git repo etc)
  - `sudo /opt/LiveBootUtils/lbu_cli.py rebuild-sfs `_`/path/to/xx-component.sfs`_
- build a new SFS file from component sources
  - `sudo /opt/LiveBootUtils/lbu_cli.py rebuild-sfs `_`/path/to/xx-component.sfs https://github.com/SFSLive/xx-component-sfs.git env1=val1 env2=val2`_

### Updating SFS components from upstream archive

- Automatically update components which have changed
  - `sudo /opt/LiveBootUtils/lbu_cli.py update-sfs `_`https://mycompany.com/sfs-repo/`_
- Automatically update running system's currently used components (based on their git sources, or `.check-up-to-date` script)
  - `sudo /opt/LiveBootUtils/lbu_cli.py update-sfs --auto-rebuild`
