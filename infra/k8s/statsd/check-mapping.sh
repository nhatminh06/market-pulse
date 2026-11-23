#!/bin/sh
set -eu
root=$(CDPATH= cd "$(dirname "$0")/../../.." && pwd)
cmp "$root/observability/statsd-mappings.yml" "$root/infra/k8s/statsd/statsd-mappings.yml"
