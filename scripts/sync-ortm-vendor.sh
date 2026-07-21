#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_repo=${ORTM_SOURCE_REPO:-"${repo_root}/../open-raster-timing-marker"}
source_ref=${ORTM_SOURCE_REF:-HEAD}
destination="${repo_root}/vendor/open-raster-timing-marker"
public_destination="${repo_root}/public/vendor/ortm"

if [[ ! -d "${source_repo}/.git" ]]; then
  echo "ORTM source repository not found: ${source_repo}" >&2
  exit 1
fi

source_commit=$(git -C "${source_repo}" rev-parse "${source_ref}^{commit}")
temporary_dir=$(mktemp -d)
trap 'rm -rf "${temporary_dir}"' EXIT

git -C "${source_repo}" archive "${source_commit}" \
  src/python/ortm \
  src/js/ortm.js \
  src/js/raster.js \
  vectors/ortm-v0.json \
  | tar -x -C "${temporary_dir}"

rm -rf "${destination}"
mkdir -p "${destination}"
cp -R "${temporary_dir}/src" "${destination}/src"
cp -R "${temporary_dir}/vectors" "${destination}/vectors"
printf '%s\n' "${source_commit}" > "${destination}/SOURCE_COMMIT"

rm -rf "${public_destination}"
mkdir -p "${public_destination}"
cp "${destination}/src/js/ortm.js" "${public_destination}/ortm.js"
cp "${destination}/src/js/raster.js" "${public_destination}/raster.js"
cp "${destination}/SOURCE_COMMIT" "${public_destination}/SOURCE_COMMIT"

echo "Synced ORTM ${source_commit} to ${destination}"
