#!/bin/sh

dbus-send --system --dest=org.freedesktop.NetworkManager --type=method_call --print-reply /org/freedesktop/NetworkManager org.freedesktop.NetworkManager.Enable boolean:false
