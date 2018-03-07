#!/usr/bin/python

import gi
import lbu_common

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

from logging import warn


class AppWindow(Gtk.ApplicationWindow):
    sfs_store_cols = [('file', object), ('file-path', str), ('mnt', str), ('stamp', str), ('icon-name', str),
                      ('need-update', bool), ('update-reason', str), ('update-icon', str),
                      ('latest-stamp', str), ('git-commit', str), ('git-source', str), ('git-source-commit', str)]

    def store_col_idx(self, name):
        for i, c in enumerate(self.sfs_store_cols):
            if c[0] == name:
                return i

    def sfs_store_append(self, **vals):
        return self.sfs_store.get_path(self.sfs_store.append(map(lambda x: vals.get(x[0]), self.sfs_store_cols)))

    def __init__(self, *args, **kwargs):
        Gtk.ApplicationWindow.__init__(self, *args, **kwargs)
        self.set_default_size(1024, 500)
        self.set_icon_name('package-x-generic')

        self.sfs_store = Gtk.ListStore(*map(lambda c: c[1], self.sfs_store_cols))
        sw = Gtk.ScrolledWindow()
        self.add(sw)
        self.tv = Gtk.TreeView(self.sfs_store)
        name_render = Gtk.CellRendererText()
        file_icon_render = Gtk.CellRendererPixbuf()
        update_icon_render = Gtk.CellRendererPixbuf()
        update_text_render = Gtk.CellRendererText()

        tvc = Gtk.TreeViewColumn("Mount")
        tvc.pack_start(file_icon_render, False)
        tvc.pack_start(name_render, True)
        tvc.set_attributes(name_render, text=self.store_col_idx('mnt'))
        tvc.set_attributes(file_icon_render, icon_name=self.store_col_idx('icon-name'))
        self.tv.append_column(tvc)

        self.tv.append_column(
            Gtk.TreeViewColumn("Backend", Gtk.CellRendererText(), text=self.store_col_idx('file-path')))
        tvc = Gtk.TreeViewColumn("Up to date")
        tvc.pack_start(update_icon_render, False)
        tvc.pack_start(update_text_render, True)
        tvc.set_attributes(update_icon_render, icon_name=self.store_col_idx('update-icon'))
        tvc.set_attributes(update_text_render, text=self.store_col_idx('update-reason'))
        self.tv.append_column(tvc)
        self.tv.append_column(Gtk.TreeViewColumn("Stamp", Gtk.CellRendererText(), text=self.store_col_idx('stamp')))
        self.tv.append_column(
            Gtk.TreeViewColumn("Source", Gtk.CellRendererText(), text=self.store_col_idx('git-source')))
        self.tv.append_column(
            Gtk.TreeViewColumn("Commit", Gtk.CellRendererText(), text=self.store_col_idx('git-commit')))
        sw.add(self.tv)
        self.show_all()

    def do_sfs_check(self, store_path):
        row = self.sfs_store[store_path]
        sfs = row[self.store_col_idx('file')]
        try:
            row[self.store_col_idx('stamp')] = lbu_common.stamp2txt(sfs.create_stamp)
            if sfs.git_source:
                row[self.store_col_idx('git-source')] = "%s#%s" % (sfs.git_source, sfs.git_branch)
                row[self.store_col_idx('git-commit')] = sfs.git_commit
            curlink = sfs.curlink_sfs()
        except Exception as e:
            warn("Error checking %r: %s", sfs, e)
            row[self.store_col_idx('update-icon')] = 'network-error'
            row[self.store_col_idx('update-reason')] = 'Error: %s' % (e,)
            return
        if curlink.realpath() == sfs.realpath():
            row[self.store_col_idx('update-icon')] = 'image-loading'
            row[self.store_col_idx('update-reason')] = 'Check scheduled..'
            return self.do_update_check, store_path
        else:
            row[self.store_col_idx('update-icon')] = 'software-update-urgent'
            row[self.store_col_idx('update-reason')] = 'Have more current file: %s' % (curlink.realpath().basename,)

    def do_update_check(self, store_path):
        row = self.sfs_store[store_path]
        row[self.store_col_idx('update-reason')] = 'Checking..'
        sfs = row[self.store_col_idx('file')]
        try:
            need_update = sfs.needs_update
        except Exception as e:
            row[self.store_col_idx('update-icon')] = 'network-error'
            row[self.store_col_idx('update-reason')] = "Check failed: %s: %s" % (e.__class__.__name__, e,)
            warn("Error during updating %r: %s", sfs, e)
        else:
            if need_update:
                row[self.store_col_idx('update-icon')] = 'software-update-available'
                row[self.store_col_idx('update-reason')] = "Latest stamp: %r" % (
                    lbu_common.stamp2txt(sfs.latest_stamp, ))
            else:
                row[self.store_col_idx('update-icon')] = 'gtk-apply'
                row[self.store_col_idx('update-reason')] = 'Up to date.'


class Application(Gtk.Application):
    window = None
    more_to_check = None

    def do_startup(self):
        Gtk.Application.do_startup(self)

    def do_activate(self):
        if self.window is None:
            self.window = AppWindow(application=self, title="Live Boot Utils")
        self.window.present()
        self.update_sfs_info()

    def do_check_more(self):
        if not self.more_to_check:
            return False
        check_method, check_path = self.more_to_check.pop(0)
        try:
            more_checks = check_method(check_path)
        except Exception as e:
            warn("Check %r for %s failed: %s", check_method, check_path, e)
        else:
            if more_checks:
                self.more_to_check.append(more_checks)
        return True if self.more_to_check else False

    def update_sfs_info(self):
        self.window.sfs_store.clear()
        self.more_to_check = []

        for branch in lbu_common.MountPoint('/').aufs_components:
            data = {"mnt": branch.path, "icon-name": "folder"}
            mpt = branch.mountpoint
            check_more = False
            if mpt == branch:
                try:
                    backend = lbu_common.FSPath(mpt.loop_backend)
                except lbu_common.NotLoopDev:
                    pass
                else:
                    data['file'] = backend
                    data['file-path'] = backend.path
                    if isinstance(backend, lbu_common.SFSFile):
                        data['icon-name'] = 'package-x-generic'
                        check_more = True
                    else:
                        data['icon-name'] = 'drive-harddisk'

            store_path = self.window.sfs_store_append(**data)
            if check_more:
                self.more_to_check.append((self.window.do_sfs_check, store_path))

        if self.more_to_check:
            GLib.idle_add(self.do_check_more)


if __name__ == '__main__':
    import logging

    logging.root.setLevel(logging.DEBUG)
    app = Application()
    app.run()
