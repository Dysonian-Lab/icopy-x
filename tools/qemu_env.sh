#!/bin/bash
# Maintainer-operated Linux environment for trusted QEMU validation commands.
# Usage: sudo bash tools/qemu_env.sh IMAGE QEMU PROJECT NEW_RUN_DIR -- COMMAND...
# No global mounts, image writes or package installation are performed.
set -euo pipefail

if [ "${1:-}" != --inside ]; then
    if [ "$#" -lt 6 ] || [ "$5" != -- ]; then
        echo "Usage: sudo bash $0 IMAGE QEMU PROJECT NEW_RUN_DIR -- COMMAND..." >&2
        exit 2
    fi
    if [ "$(id -u)" -ne 0 ] || [ -z "${SUDO_USER:-}" ] || [ "$SUDO_USER" = root ]; then
        echo "Run with sudo from the non-root account that will execute tests." >&2
        exit 2
    fi
    image=$(realpath -e "$1")
    qemu=$(realpath -e "$2")
    project=$(realpath -e "$3")
    run=$(realpath -m "$4")
    shift 5
    script=$(realpath -e "$0")
    [ -f "$image" ] && [ -x "$qemu" ]
    [ "$(git -c "safe.directory=$project" -C "$project" rev-parse --show-toplevel)" = "$project" ]
    # /mnt and /tmp become private filesystems inside the namespace.
    for path in "$image" "$qemu" "$project" "$run" "$script"; do
        case "$path" in
            /mnt|/mnt/*|/tmp|/tmp/*) echo "Inputs/output must be outside /mnt and /tmp: $path" >&2; exit 2 ;;
            *','*|*':'*|*'\'*|*$'\n'*) echo "Unsupported mount-option characters in path: $path" >&2; exit 2 ;;
        esac
    done
    if [ -e "$run" ] || [ -L "$run" ]; then
        echo "Run directory must be new: $run" >&2
        exit 2
    fi
    mkdir -m 755 "$run"
    # Validate the factory image's root partitions and record immutable inputs.
    python3 - "$image" "$qemu" "$project" "$run" "$SUDO_USER" "$script" "$@" <<'PY'
import hashlib, json, os, pathlib, pwd, subprocess, sys
image, qemu, project, run, user, script = sys.argv[1:7]
def sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
table = json.loads(subprocess.check_output(['sfdisk', '--json', image]))['partitiontable']
if table.get('unit') != 'sectors' or table.get('sectorsize', 512) != 512:
    raise SystemExit('Expected a 512-byte-sector factory SD image')
parts = table['partitions']
if len(parts) < 3 or any(str(p.get('type')) != '83' for p in parts[1:3]):
    raise SystemExit('Expected Linux root partitions 2 and 3')
size = os.stat(image).st_size
roots = [{'offset': p['start'] * 512, 'size': p['size'] * 512} for p in parts[1:3]]
if any(p['offset'] <= 0 or p['size'] <= 0 or p['offset'] + p['size'] > size for p in roots):
    raise SystemExit('Root partition lies outside the image')
if roots[0]['offset'] + roots[0]['size'] > roots[1]['offset']:
    raise SystemExit('Root partitions overlap or are out of order')
account = pwd.getpwnam(user)
manifest = dict(image=image, image_sha256=sha(image), image_bytes=size,
                qemu=qemu, qemu_sha256=sha(qemu),
                qemu_version=subprocess.check_output([qemu, '--version'], text=True).splitlines()[0],
                project=project, source_commit=subprocess.check_output(
                    ['git', '-c', 'safe.directory='+project, '-C', project, 'rev-parse', 'HEAD'], text=True).strip(),
                source_status=subprocess.check_output(
                    ['git', '-c', 'safe.directory='+project, '-C', project, 'status', '--porcelain'], text=True),
                user=user, uid=account.pw_uid, gid=account.pw_gid, roots=roots,
                harness_sha256=sha(script),
                source_diff_sha256=hashlib.sha256(subprocess.check_output(
                    ['git', '-c', 'safe.directory='+project, '-C', project, 'diff', 'HEAD', '--binary'])).hexdigest(),
                command=sys.argv[7:], status='PREPARED', overlay_xino='off')
pathlib.Path(run, 'environment.json').write_text(json.dumps(manifest, indent=2)+'\n')
PY
    echo "Run directory: $run"
    # Killing the namespace init also kills its descendants. Loop mounts below
    # use autoclear, so even forced namespace teardown releases their devices.
    # Bash does not forward a signal sent only to this wrapper while waiting
    # for unshare. Kill the owned namespace supervisor explicitly; --kill-child
    # then tears down its PID namespace, including detached test processes.
    namespace_pid=
    interrupted=0
    interrupt_namespace() {
        interrupted=$1
        if [ -n "$namespace_pid" ]; then
            kill -KILL "$namespace_pid" 2>/dev/null || true
        fi
    }
    trap 'interrupt_namespace 130' INT
    trap 'interrupt_namespace 143' TERM
    trap 'interrupt_namespace 129' HUP
    set +e
    unshare --mount --pid --fork --kill-child --mount-proc --propagation private \
        bash "$script" --inside "$run" &
    namespace_pid=$!
    if [ "$interrupted" -ne 0 ]; then interrupt_namespace "$interrupted"; fi
    wait "$namespace_pid"
    rc=$?
    if [ "$interrupted" -ne 0 ]; then
        wait "$namespace_pid" 2>/dev/null
        rc=$interrupted
    fi
    trap - INT TERM HUP
    set -e
    python3 - "$run" "$rc" "$interrupted" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1], 'environment.json')
data = json.loads(p.read_text())
status = 'COMMAND_COMPLETED' if sys.argv[2]=='0' else 'COMMAND_FAILED'
data.update(status='INTERRUPTED' if sys.argv[3]!='0' else status,
            exit_code=int(sys.argv[2]))
p.write_text(json.dumps(data, indent=2)+'\n')
PY
    exit "$rc"
fi

run="$2"
mapfile -d '' -t config < <(python3 - "$run/environment.json" <<'PY'
import json, sys
m=json.load(open(sys.argv[1]))
values=[m['image'],m['qemu'],m['project'],m['user'],m['uid'],m['gid'],
        m['roots'][0]['offset'],m['roots'][0]['size'],m['roots'][1]['offset'],m['roots'][1]['size']]
for value in values + m['command']:
    sys.stdout.buffer.write(str(value).encode()+b'\0')
PY
)
image=${config[0]}; qemu=${config[1]}; project=${config[2]}; user=${config[3]}
uid=${config[4]}; gid=${config[5]}
command=("${config[@]:10}")
mounts=()
cleanup() {
    local rc=$?
    trap - EXIT
    set +e
    for ((index=${#mounts[@]}-1; index>=0; index--)); do
        if ! umount "${mounts[index]}"; then rc=1; fi
    done
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
ulimit -c 0
mkdir "$run/base1" "$run/base2" "$run/upper1" "$run/upper2" "$run/work1" "$run/work2" "$run/upan" "$run/cache" "$run/results"
chown "$uid:$gid" "$run/upan" "$run/cache" "$run/results"
for index in 1 2; do
    offset_index=$((4+index*2)); size_index=$((offset_index+1))
    mount -t ext4 -o "loop,ro,noload,offset=${config[offset_index]},sizelimit=${config[size_index]}" "$image" "$run/base$index"
    mounts+=("$run/base$index")
    device=$(findmnt -rn -o SOURCE --target "$run/base$index")
    block=${device##*/}
    [ "$(cat "/sys/block/$block/ro")" = 1 ]
    [ "$(cat "/sys/block/$block/loop/autoclear")" = 1 ]
done
mount -t tmpfs -o mode=755 tmpfs /mnt
mounts+=(/mnt)
mkdir -p /mnt/sdcard/root1 /mnt/sdcard/root2 /mnt/upan
for index in 1 2; do
    # The old ARM FreeType build cannot handle overlay's encoded 64-bit inode
    # values. xino=off is required for real font enumeration and Tk rendering.
    mount -t overlay overlay -o "lowerdir=$run/base$index,upperdir=$run/upper$index,workdir=$run/work$index,xino=off" \
        "/mnt/sdcard/root$index"
    mounts+=("/mnt/sdcard/root$index")
done
mount --bind "$run/upan" /mnt/upan
mounts+=(/mnt/upan)
mount -t tmpfs -o mode=1777,size=512m tmpfs /tmp
mounts+=(/tmp)
mkdir /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix
rootfs=/mnt/sdcard/root2/root
arm_python="$rootfs/usr/local/python-3.8.0/bin/python3.8"
[ -x "$arm_python" ]
cat > /tmp/icopy-fonts.conf <<'FONTS'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>/mnt/sdcard/root2/root/home/pi/ipk_app_main/res/font</dir>
  <dir>/mnt/sdcard/root1/usr/share/fonts</dir>
  <cachedir>/tmp/icopy-font-cache</cachedir>
</fontconfig>
FONTS
sha256sum "$arm_python" /tmp/icopy-fonts.conf \
    "$rootfs/home/pi/ipk_app_main/res/font/mononoki-Regular.ttf" \
    "$rootfs/home/pi/ipk_app_main/res/font/monozhwqy.ttf" > "$run/guest-inputs.sha256"
cd "$project"
runuser -u "$user" -- env \
    PROJECT="$project" QEMU_BIN="$qemu" QEMU_LD_PREFIX="$rootfs" \
    QEMU_PYTHON="$arm_python" ICOPY_QEMU_RUN="$run" \
    QEMU_SET_ENV="LD_LIBRARY_PATH=$rootfs/usr/local/python-3.8.0/lib:$rootfs/usr/lib/arm-linux-gnueabihf:$rootfs/lib/arm-linux-gnueabihf:/mnt/sdcard/root1/usr/lib/arm-linux-gnueabihf:/mnt/sdcard/root1/lib/arm-linux-gnueabihf" \
    FONTCONFIG_FILE=/tmp/icopy-fonts.conf XDG_CACHE_HOME="$run/cache" \
    PYTHONDONTWRITEBYTECODE=1 \
    "${command[@]}" 2>&1 | tee "$run/command.log"
