#!/usr/bin/env bash
# Unsigned macOS validation packaging only. Linux, Windows and signed releases do not call this.
# Tauri 2.12.1 writes bundle_dmg.sh output to bundle/macos/rw.<pid>.<name>.dmg and mounts it with
# `hdiutil attach -mountrandom /Volumes`. A transient "Resource busy" leaves that image attached
# and Tauri hides the script unless --verbose is set. Retry the same build once after detaching
# only those volumes. Bash 3.2 (the macOS runner default) is the target shell.
set -euo pipefail

# our_dmg_image BUNDLE_ROOT IMAGE_PATH
# True when IMAGE_PATH is a .dmg placed directly in this job's bundle/macos or bundle/dmg.
our_dmg_image() {
  local bundle_root="$1"
  local image_path="$2"
  local root rel base
  [[ -n "$image_path" && "$image_path" != "<none>" ]] || return 1
  case "$image_path" in
    *.dmg) ;;
    *) return 1 ;;
  esac
  case "$image_path" in
    *"/.."*|*"//"*) return 1 ;;
  esac
  if [[ -d "$bundle_root" ]]; then
    root="$(cd "$bundle_root" && pwd -P)"
  else
    root="${bundle_root%/}"
  fi
  if [[ -e "$image_path" ]]; then
    image_path="$(cd "$(dirname "$image_path")" && pwd -P)/$(basename "$image_path")"
  fi
  [[ "$image_path" == "$root"/* ]] || return 1
  rel="${image_path#"$root"/}"
  case "$rel" in
    macos/*) base="${rel#macos/}" ;;
    dmg/*) base="${rel#dmg/}" ;;
    *) return 1 ;;
  esac
  [[ "$base" != */* && "$base" == *.dmg ]]
}

# mount_point_from_device_line
# Prints the /Volumes mount from one `hdiutil info` device line, or fails when that line has none.
# attach's columns are: device, tab, content hint, tab, mount point.
mount_point_from_device_line() {
  local line="$1"
  local mount
  line="${line%"${line##*[![:space:]]}"}"
  [[ "$line" =~ (/Volumes/[^/][^/]*)$ ]] || return 1
  mount="${BASH_REMATCH[1]}"
  mount="${mount%"${mount##*[![:space:]]}"}"
  [[ -n "$mount" && "$mount" != "/Volumes" && "$mount" != "/Volumes/" ]] || return 1
  [[ "${mount#/Volumes/}" != */* && "${mount#/Volumes/}" != "." && "${mount#/Volumes/}" != ".." ]] || return 1
  printf '%s\n' "$mount"
}

# already_recorded NEEDLE \n RECORD
# True when NEEDLE is one whole line of RECORD. Matching is literal.
already_recorded() {
  local needle="$1"
  local record="$2"
  local previous
  while IFS= read -r previous || [[ -n "$previous" ]]; do
    [[ "$previous" == "$needle" ]] && return 0
  done <<END
$record
END
  return 1
}

# list_mounts_to_detach BUNDLE_ROOT
# Reads `hdiutil info` text on stdin. Prints each mount point under /Volumes that belongs to a
# DMG in BUNDLE_ROOT, once. Other attached disks are left alone.
list_mounts_to_detach() {
  local bundle_root="$1"
  local line image_path="" seen="" mount
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    if [[ "$line" =~ ^image-path[[:space:]]*:[[:space:]]*(.*)$ ]]; then
      image_path="${BASH_REMATCH[1]}"
      image_path="${image_path%"${image_path##*[![:space:]]}"}"
      continue
    fi
    [[ "$line" =~ ^/dev/disk[0-9] ]] || continue
    our_dmg_image "$bundle_root" "$image_path" || continue
    mount="$(mount_point_from_device_line "$line")" || continue
    already_recorded "$mount" "$seen" && continue
    seen="${seen}${mount}"$'\n'
    printf '%s\n' "$mount"
  done
}

# collect_partial_dmg_paths BUNDLE_ROOT
# NUL-separated partial images: every *.dmg directly in bundle/dmg, and rw.*.dmg directly
# under bundle/macos or bundle/dmg. The packaged .app is not a match.
collect_partial_dmg_paths() {
  local bundle_root="$1"
  if [[ -d "$bundle_root/dmg" ]]; then
    find "$bundle_root/dmg" -mindepth 1 -maxdepth 1 -type f -name '*.dmg' -print0
  fi
  if [[ -d "$bundle_root" ]]; then
    find "$bundle_root" -mindepth 2 -maxdepth 2 -type f -name 'rw.*.dmg' -print0
  fi
}

remove_partial_dmgs() {
  local bundle_root="$1"
  local path seen="" found=0
  while IFS= read -r -d '' path; do
    already_recorded "$path" "$seen" && continue
    seen="${seen}${path}"$'\n'
    found=1
    echo "Removing partial DMG ${path}"
    rm -f -- "$path"
  done < <(collect_partial_dmg_paths "$bundle_root")
  if [[ "$found" -eq 0 ]]; then
    echo "No partial DMG files to remove."
  fi
}

detach_our_volumes() {
  local bundle_root="$1"
  local info mount
  info="$(hdiutil info)" || {
    echo "hdiutil info exited $? while choosing volumes; continuing to the retry." >&2
    return 0
  }
  while IFS= read -r mount; do
    [[ -n "$mount" ]] || continue
    echo "Detaching leftover DMG volume ${mount}"
    if ! hdiutil detach "$mount"; then
      echo "hdiutil detach failed for ${mount}; trying once with -force." >&2
      if ! hdiutil detach -force "$mount"; then
        echo "Could not detach ${mount}." >&2
      fi
    fi
  done < <(printf '%s\n' "$info" | list_mounts_to_detach "$bundle_root")
}

package_build_command() {
  local bundles="$1"
  printf '%s\0' npm run tauri -- build --bundles "$bundles" --verbose -- --locked
}

main() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "package-unsigned-macos.sh runs only on macOS." >&2
    exit 2
  fi
  local bundles="${1:?Pass the Tauri --bundles value}"
  local desktop_root bundle_root status word
  desktop_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  cd "$desktop_root" || exit 1
  bundle_root="$desktop_root/src-tauri/target/release/bundle"
  local build_cmd=()
  while IFS= read -r -d '' word; do
    build_cmd[${#build_cmd[@]}]="$word"
  done < <(package_build_command "$bundles")
  status=0
  "${build_cmd[@]}" || status=$?
  if [[ "$status" -eq 0 ]]; then
    exit 0
  fi
  echo "Unsigned macOS package failed with exit ${status}." >&2
  echo "---- hdiutil info ----"
  hdiutil info || echo "hdiutil info exited $?" >&2
  echo "---- end hdiutil info ----"
  detach_our_volumes "$bundle_root"
  remove_partial_dmgs "$bundle_root"
  echo "Waiting 10 seconds before one unsigned macOS package retry." >&2
  sleep 10
  echo "Retrying unsigned macOS package." >&2
  "${build_cmd[@]}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
