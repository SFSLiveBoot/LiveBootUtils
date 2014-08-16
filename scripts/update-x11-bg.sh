#!/bin/sh

d="$HOME/.cache/wallpaper"
test -d "$d" || mkdir -p "$d"

modded="$d/modified-with-ip"
orig=""

gen_draw_args() {
  local ps=${1:-24} dev_x dev ps_x
  local loc_y="$ps"
  local iface_list="$(ip a | grep -o '^[0-9]\+:[^:]\+:\|inet [^ ]*' | cut -f2 -d" " | grep -v 127.0.0.1 | cut -f1 -d/)"
  local out_ip="$(ip route get 8.8.8.8 | grep -o 'src [^ ]*' | cut -f2 -d" ")"
  for data in $iface_list;do
    case "$data" in
      *:) dev="$data";;
      *) 
        dev_x="$dev"
        ps_x="$ps"
        test "$data" != "$out_ip" || { ps_x=$(($ps*2)) ; dev_x=""; }
        echo -pointsize $ps_x -annotate +50+$loc_y $dev_x$data 
        loc_y=$(($loc_y+$ps_x+5))
        ;;
    esac
  done
}

if gsettings writable org.gnome.desktop.background picture-uri >/dev/null ;then
  get_orig() { test ! -r "$d/.orig" || cat "$d/.orig"; }
  set_orig() { echo "$1" >"$d/.orig"; }
  get_wp() {
    local ret="$(gsettings get org.gnome.desktop.background picture-uri | sed -e "s@^'file://@@" -e "s/'$//")"
    case "$ret" in *.xml) ret="$(grep -Eo '/[^<>]*\.(jpg|png)' "$ret" | head -1)" ;;esac
    echo "$ret"
  }
  set_wp() { gsettings set org.gnome.desktop.background picture-uri "file://$1"; }
  gsettings set org.gnome.desktop.background picture-options stretched
else
  get_orig() { gconftool --get /desktop/gnome/background/picture_filename_orig; }
  set_orig() { gconftool --set /desktop/gnome/background/picture_filename_orig -t string "$1"; }
  get_wp() { gconftool --get /desktop/gnome/background/picture_filename; }
  set_wp() { gconftool --set /desktop/gnome/background/picture_filename -t string "$1"; }
fi

check_wp_settings() {
  local wp_set="$(get_wp)"
  orig="$(get_orig)"
  test -e "$orig" -a "x$wp_set" = "x$modded" || {
    if test "x$wp_set" = "x$modded";then
      echo "Lost the track of original wallpaper..." >&2
      return 1
    elif test ! -e "$wp_set";then
      echo "Wallpaper not set or readable?" >&2
      return 1
    else
      orig="$wp_set"
      set_orig "$orig"
    fi
  }
}

update_bg() {
  if check_wp_settings;then
    convert "$orig" -fill white -font Arial-Normal -gravity NorthEast $(gen_draw_args $(($(identify -format %h "$orig")/30)))  "$modded" || return
    set_wp "$modded"
  fi
}

pidfile="$HOME/.update-x11-bg-monitor.pid"

update_bg

if test "monitor" = "$1";then
  test ! -e "$pidfile" || test ! -d "/proc/$(cat "$pidfile")" || { echo "Already running, not starting monitor" >&2; exit 1; }
  echo "$$" >"$pidfile"
  while true; do
    ip monitor | head -1 >/dev/null
    sleep 1
    update_bg
  done
fi
