#!/bin/bash
# Run in its own process: failure must never exit the persistent session shell.
set -euo pipefail
CONFIG=${STORAGE_CONFIG_FILE:-/app/storage.json}
MOUNT=${STORAGE_MOUNT:-/mnt/shared}
[[ -f "$CONFIG" ]] || { echo 'ERROR: Storage is not configured. Launch a new VM after deploying.' >&2; exit 1; }
FS_ID=$(jq -er .file_system_id "$CONFIG")
TARGET=$(jq -er .mount_target_ip "$CONFIG")
export AWS_REGION=$(jq -er .region "$CONFIG")
export AWS_DEFAULT_REGION="$AWS_REGION"

mounted() {
  # A directory existing is not evidence of a mount. Reject local disk writes.
  local kind
  kind=$(findmnt -rn -M "$MOUNT" -o FSTYPE) || return 1
  [[ "$kind" == nfs || "$kind" == nfs4 ]]
}

case "${1:-}" in
  mount)
    mkdir -p "$MOUNT"
    if ! mounted; then
      echo "NOTE: Mounting S3 Files $FS_ID at $MOUNT..."
      # Explicit mount target avoids EC2 availability-zone metadata discovery.
      # S3 Files requires TLS + IAM; never downgrade to anonymous raw NFS.
      if ! timeout 90 mount -t s3files -o "region=$AWS_REGION,mounttargetip=$TARGET,nodirects3read" "$FS_ID:/" "$MOUNT"; then
        echo 'ERROR: S3 Files mount failed. Inspect /var/log/amazon/efs/mount.log inside the VM.' >&2
        exit 1
      fi
    fi
    mounted || { echo 'ERROR: /mnt/shared is not an NFS mount.' >&2; exit 1; }
    # The guest supervisor is PID 1, not systemd. Run the utility's watchdog
    # explicitly so IAM certificates can refresh during a long session.
    if [[ ! -f /run/s3files-watchdog.pid ]] || ! kill -0 "$(cat /run/s3files-watchdog.pid)" 2>/dev/null; then
      nohup amazon-efs-mount-watchdog </dev/null >/var/log/amazon/efs/watchdog-console.log 2>&1 &
      echo $! >/run/s3files-watchdog.pid
    fi
    findmnt -rn -M "$MOUNT" -o TARGET,FSTYPE,SOURCE
    ;;
  check)
    mounted || { echo "ERROR: Shared storage is not mounted yet; initialization may still be running. Inspect /var/log/amazon/efs/mount.log if this persists." >&2; exit 1; }
    timeout 30 stat "$MOUNT" >/dev/null
    findmnt -rn -M "$MOUNT" -o TARGET,FSTYPE,SOURCE
    ;;
  write)
    mounted || { echo 'ERROR: Shared storage is not ready; refusing to write to local disk.' >&2; exit 1; }
    NAME="${2:-demo.txt}"
    [[ "$NAME" =~ ^[a-zA-Z0-9_-]+\.txt$ ]] || { echo 'ERROR: Use a simple .txt filename.' >&2; exit 1; }
    mkdir -p "$MOUNT/microvm-demo"
    printf 'Written through NFS from MicroVM %s\n' "$(jq -er .microvm_id "$CONFIG")" > "$MOUNT/microvm-demo/$NAME"
    sync -f "$MOUNT/microvm-demo/$NAME"
    cat "$MOUNT/microvm-demo/$NAME"
    sha256sum "$MOUNT/microvm-demo/$NAME"
    echo "NOTE: S3 key: microvm-demo/$NAME"
    echo 'NOTE: S3 Files exports asynchronously; allow about a minute, sometimes longer.'
    ;;
  read)
    mounted || { echo 'ERROR: /mnt/shared is not an NFS mount.' >&2; exit 1; }
    NAME="${2:-demo.txt}"
    [[ "$NAME" =~ ^[a-zA-Z0-9_-]+\.txt$ ]] || { echo 'ERROR: Use a simple .txt filename.' >&2; exit 1; }
    cat "$MOUNT/microvm-demo/$NAME"
    ;;
  *) echo 'Usage: storage.sh mount | check | write [name.txt] | read [name.txt]' >&2; exit 2 ;;
esac
