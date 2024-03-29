#!/usr/bin/env python3

import gi
import os
import traceback

import lbu_common
from lbu_common import cached_property

gi.require_version('Gtk', '3.0')
gi.require_version('Vte', '2.91')
from gi.repository import Gtk, GLib, Pango, Vte

from logging import warn

import threading

class AppWindow(Gtk.ApplicationWindow):
    sfs_store_cols = [('file', object), ('file-path', str), ('mnt', str), ('stamp', str), ('icon-name', str),
                      ('need-update', bool), ('update-reason', str), ('update-icon', str),
                      ('latest-stamp', str), ('git-commit', str), ('git-source', str), ('git-source-commit', str),
                      ('git-source-save', str)]

    def store_col_idx(self, name):
        for i, c in enumerate(self.sfs_store_cols):
            if c[0] == name:
                return i

    def sfs_store_append(self, **vals):
        return self.sfs_store.get_path(self.sfs_store.append([vals.get(x[0]) for x in self.sfs_store_cols]))

    def __init__(self, *args, **kwargs):
        Gtk.ApplicationWindow.__init__(self, *args, **kwargs)
        self.set_default_size(1024, 500)
        self.set_icon_name('package-x-generic')

        self.sfs_store = Gtk.ListStore(*[c[1] for c in self.sfs_store_cols])
        sw = Gtk.ScrolledWindow()
        vbox = Gtk.Box()
        vbox.set_orientation(Gtk.Orientation.VERTICAL)
        vbox.pack_start(sw, True, True, 0)
        self.add(vbox)
        self.tv = Gtk.TreeView(model=self.sfs_store)
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
        self.upd_tvc = tvc = Gtk.TreeViewColumn("Up to date")
        tvc.pack_start(update_icon_render, False)
        tvc.pack_start(update_text_render, True)
        update_text_render.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        tvc.set_attributes(update_icon_render, icon_name=self.store_col_idx('update-icon'))
        tvc.set_attributes(update_text_render, text=self.store_col_idx('update-reason'))
        tvc.set_resizable(True)
        tvc.set_min_width(300)
        self.tv.connect("query-tooltip", self.on_query_tooltip)
        self.tv.set_has_tooltip(True)
        self.tv.append_column(tvc)
        self.tv.append_column(Gtk.TreeViewColumn("Stamp", Gtk.CellRendererText(), text=self.store_col_idx('stamp')))
        src_rndr = Gtk.CellRendererText()
        src_rndr.set_property("editable", True)
        src_rndr.connect("edited", self.on_store_edited, 'git-source', 'git-source-save')
        src_col = Gtk.TreeViewColumn("Source")
        src_icon_rndr = Gtk.CellRendererPixbuf()
        src_col.pack_start(src_icon_rndr, False)
        src_col.pack_start(src_rndr, True)
        src_col.set_attributes(src_rndr, text=self.store_col_idx('git-source'))
        src_col.set_cell_data_func(src_icon_rndr, self.on_render_source_icon)
        self.tv.append_column(src_col)
        self.tv.append_column(
            Gtk.TreeViewColumn("Commit", Gtk.CellRendererText(), text=self.store_col_idx('git-commit')))
        sw.add(self.tv)

        buttonbox = Gtk.Box()
        vbox.pack_start(buttonbox, False, True, 0)
        self.refresh_button = Gtk.Button.new_with_label("Refresh")
        buttonbox.pack_start(self.refresh_button, False, False, 0)
        self.show_all()

    def on_render_source_icon(self, tvc, rndr, store, treeiter, x):
        if store[treeiter][self.store_col_idx('git-source-save')]:
            rndr.set_property('icon-name', 'text-editor')
        else:
            rndr.set_property('icon-name', None)

    def on_store_edited(self, rndr, path, new_text, colname, save_col):
        if save_col:
            save_val = self.sfs_store[path][self.store_col_idx(save_col)]
            if not new_text and save_val:
                self.sfs_store[path][self.store_col_idx(colname)] = save_val
                self.sfs_store[path][self.store_col_idx(save_col)] = None
                return
            if not save_val:
                self.sfs_store[path][self.store_col_idx(save_col)] = self.sfs_store[path][self.store_col_idx(colname)]
        self.sfs_store[path][self.store_col_idx(colname)] = new_text

    def on_query_tooltip(self, tv, x, y, kbd_mode, tooltip):
        has_row, tx, ty, model, path, iter = tv.get_tooltip_context(x, y, kbd_mode)
        if not has_row:
            return
        tvc_path, tvc, x, y = tv.get_path_at_pos(tx, ty)
        if tvc == self.upd_tvc:
            txt = model[iter][self.store_col_idx("update-reason")]
        else:
            txt = model[iter][self.store_col_idx("mnt")]
        if txt:
            tv.set_tooltip_text(txt)

    def do_sfs_check(self, store_path):
        row = self.sfs_store[store_path]
        sfs = row[self.store_col_idx('file')]
        try:
            row[self.store_col_idx('stamp')] = lbu_common.stamp2txt(sfs.create_stamp)
            if sfs.git_source:
                row[self.store_col_idx('git-source')] = "%s%s" % (
                    sfs.git_source, "" if sfs.git_branch is None else "#%s" % (sfs.git_branch,))
                row[self.store_col_idx('git-commit')] = sfs.git_commit
            curlink = sfs.curlink_sfs()
        except Exception as e:
            warn("Error checking %r: %s", sfs, e)
            traceback.print_exc()
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
            traceback.print_exc()
        else:
            if need_update:
                row[self.store_col_idx('update-icon')] = 'software-update-available'
                row[self.store_col_idx('update-reason')] = "Latest stamp: %r" % (
                    lbu_common.stamp2txt(sfs.latest_stamp, ))
            else:
                row[self.store_col_idx('update-icon')] = 'gtk-apply'
                row[self.store_col_idx('update-reason')] = 'Up to date.'


class Checker(threading.Thread):
    def __init__(self, *args, **kwargs):
        threading.Thread.__init__(self, *args, **kwargs)
        self.todo = []
        self.wait_lock = threading.Lock()
    def run(self):
        keep_running = True
        while keep_running:
            while self.todo:
                check_method, check_path = self.todo.pop(0)
                if check_method == None:
                    keep_running = False
                    break
                try: more = check_method(check_path)
                except Exception as e:
                    warn("cannot check %r with %r: %s", check_path, check_method, e)
                else:
                    if more:
                        self.todo.append(more)
            if keep_running:
                self.wait_lock.acquire()
        return
    def append(self, check_method, check_path=None):
        self.todo.append((check_method, check_path))
        if self.wait_lock.locked():
            self.wait_lock.release()
        if check_method and not self.is_alive():
            self.start()
    def clear(self):
        if self.todo:
            self.todo.clear()

class Application(Gtk.Application):
    window = None
    checker = Checker(daemon=True)

    @cached_property
    def lbu_cmd(self):
        cmd = [] if os.getuid() == 0 else ['/usr/bin/sudo']
        cmd.append(os.path.join(os.path.dirname(__file__), 'lbu_cli.py'))
        return cmd

    def do_startup(self):
        Gtk.Application.do_startup(self)

    def do_activate(self):
        if self.window is None:
            self.window = AppWindow(application=self, title="Live Boot Utils")
            self.window.refresh_button.connect("clicked", self.update_sfs_info)
            self.window.tv.connect('row-activated', self.on_row_activate)
        self.window.present()
        self.update_sfs_info()

    def on_row_activate(self, tv, path, tvc):
        store = tv.get_model()
        state, msg, mnt, sfs, src = [store[path][self.window.store_col_idx(n)] for n in ('update-icon', 'update-reason', 'mnt', 'file', 'git-source')]
        dlg_msg = ''
        if state == 'gtk-apply':
            dlg_msg = "Looks like %s is up to date." % (sfs.path,)
        elif state == 'network-error':
            dlg_msg = "Cannot confirm due to network error:\n%s" % (msg,)
        cmd = self.lbu_cmd + (
            ['aufs-update-branch', mnt] if state == 'software-update-urgent' else [
                'rebuild-sfs', sfs.curlink_sfs().path])
        sfs_src = "%s%s" % (sfs.git_source, "" if sfs.git_branch is None else "#%s" % (sfs.git_branch))
        if sfs_src != src: cmd.append(src)

        dlg = Gtk.MessageDialog(self.window, Gtk.DialogFlags.DESTROY_WITH_PARENT,
                                Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK_CANCEL,
                                "%s\nDo you want to run: \n%s?" % (dlg_msg, " ".join(cmd),))
        dlg.connect("response", self.on_dlg_update, cmd)
        dlg.show_all()
        dlg.run()

    def on_dlg_update(self, dlg, response, cmd):
        if response == Gtk.ResponseType.OK:
            self.run_cmd(cmd)
        dlg.destroy()

    def run_cmd(self, cmd, end_callback=None):
        term_win = Gtk.Window()
        term_win.set_title(" ".join(cmd))
        vte = Vte.Terminal()
        term_win.add(vte)
        term_win.show_all()
        spawn = vte.spawn_sync(Vte.PtyFlags.DEFAULT, os.getcwd(), cmd, [],
                               GLib.SpawnFlags.DO_NOT_REAP_CHILD, None, None)
        vte.connect("child-exited", self.on_vte_finished, term_win)

    def on_vte_finished(self, vte, ret_status, win):
        win.set_title("Finished[%d]: %s" % (ret_status, win.get_title(),))
        self.update_sfs_info()

    def update_sfs_info(self, *args):
        self.window.sfs_store.clear()
        self.checker.clear()

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
                self.checker.append(self.window.do_sfs_check, store_path)


if __name__ == '__main__':
    import logging

    # logging.root.setLevel(logging.DEBUG)
    app = Application()
    app.run()
