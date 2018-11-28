#!/bin/sh

: "${lbu:=/opt/LiveBootUtils}"
. "$lbu/scripts/common.func"

alias build-finalize="exit 0"
alias build-cancel="exit 101"

PS1='\n${sfs_build_target+Build shell for: \"${sfs_build_target}\". }Use "build-cancel" to cancel, or "build-finalize" to save changes.\n[\A][\W]\$ '
