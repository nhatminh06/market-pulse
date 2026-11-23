#!/usr/bin/env bash
set -euo pipefail

test $# -eq 1
tools_dir=$1
mkdir -p "$tools_dir"

curl -fsSL --retry 3 https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64 -o "$tools_dir/kind"
echo "aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d  $tools_dir/kind" | sha256sum -c -
curl -fsSL --retry 3 https://dl.k8s.io/release/v1.36.4/bin/linux/amd64/kubectl -o "$tools_dir/kubectl"
echo "8b8f088da2dab964f853b38464033b1be15ede2839eca751482357c45abdd05a  $tools_dir/kubectl" | sha256sum -c -
curl -fsSL --retry 3 https://github.com/fluxcd/flux2/releases/download/v2.10.1/flux_2.10.1_linux_amd64.tar.gz -o "$tools_dir/flux.tar.gz"
echo "967365ff84aabc97871827ff33713ef0c09f62dd83c18759e3a5e39adcea9285  $tools_dir/flux.tar.gz" | sha256sum -c -
tar -xzf "$tools_dir/flux.tar.gz" -C "$tools_dir" flux
curl -fsSL --retry 3 https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-linux-amd64.tar.gz -o "$tools_dir/kubeconform.tar.gz"
echo "9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883  $tools_dir/kubeconform.tar.gz" | sha256sum -c -
tar -xzf "$tools_dir/kubeconform.tar.gz" -C "$tools_dir" kubeconform
chmod 755 "$tools_dir/kind" "$tools_dir/kubectl" "$tools_dir/flux" "$tools_dir/kubeconform"
rm "$tools_dir/flux.tar.gz" "$tools_dir/kubeconform.tar.gz"
"$tools_dir/kind" version
"$tools_dir/kubectl" version --client
"$tools_dir/flux" --version
"$tools_dir/kubeconform" -v
