#!/bin/sh
set -eu

# Run after the disposable client has configured its /tmp/.mc alias.
test "$#" -eq 2 || { echo 'usage: check-object.sh KUBECONFIG EXPECTED_SHA256' >&2; exit 2; }
KUBECONFIG=$1
EXPECTED=$2
case "$EXPECTED" in *[!0123456789abcdef]*|'') echo 'expected lowercase SHA-256 required' >&2; exit 2;; esac
test "${#EXPECTED}" -eq 64 || { echo 'expected 64-character SHA-256' >&2; exit 2; }
KUBECTL=${KUBECTL:-kubectl}
test "$($KUBECTL --kubeconfig "$KUBECONFIG" config current-context)" = "${KIND_CONTEXT:-kind-market-pulse-m4}"
test "$($KUBECTL --kubeconfig "$KUBECONFIG" -n market-pulse-m4-storage get pvc/data-minio-0 -o jsonpath='{.status.phase}')" = Bound
ACTUAL=$($KUBECTL --kubeconfig "$KUBECONFIG" -n market-pulse-m4-storage exec m4-s3-client -- sh -ec 'mc --config-dir /tmp/.mc cat pilot/m4-disposable/object.txt | sha256sum' | awk '{print $1}')
test "$ACTUAL" = "$EXPECTED" || { echo 'S3 object digest mismatch' >&2; exit 1; }
printf 'S3 digest verified: %s\n' "$ACTUAL"
