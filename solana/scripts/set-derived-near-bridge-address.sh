#!/usr/bin/env bash
# Prints the Squads custom instruction that rotates omni-bridge's
# `derived_near_bridge_address` on Solana.
#
# The config field is the 64-byte secp256k1 public key the program recovers
# signatures against, so rotating it is the only way to change who can
# finalise transfers without MPC. One instruction, no funds move.
#
# usage: ./set-derived-near-bridge-address.sh <128-hex-chars>
#   e.g. ./set-derived-near-bridge-address.sh b527c206...54ca9229
#   VAULT=<vault pubkey> ./... <key>   # if the admin is not vault index 0
set -euo pipefail

PROGRAM=dahPEoZGXfyV58JqqH85okdHmpN8U2q8owgPUXSCPxe
CONFIG=2iANpDh96GgithLPTVMZjZFtnbrYBUCLqnEvaytbwTQq
DISCRIMINATOR=5b31d2e919709361
VAULT=${VAULT:-5kx8AapW8tPkiFbuHiuEBcmN3ddwpBWZfCrZgxXgWeb6}

if [ $# -ne 1 ]; then
  echo "usage: $0 <128-hex-chars>" >&2
  echo "  current value: solana account $CONFIG --output json | ... (bytes 48..112)" >&2
  exit 1
fi

KEY=$(printf '%s' "$1" | tr -d '[:space:]:' | tr 'A-F' 'a-f')
if ! printf '%s' "$KEY" | grep -Eq '^[0-9a-f]{128}$'; then
  echo "expected 64 bytes as 128 hex characters, got ${#KEY}" >&2
  exit 1
fi

DATA=$DISCRIMINATOR$KEY
B64=$(printf '%s' "$DATA" | python3 -c 'import base64,sys;sys.stdout.write(base64.b64encode(bytes.fromhex(sys.stdin.read().strip())).decode())')

cat <<EOF
program   $PROGRAM
disc      $DISCRIMINATOR  (set_derived_near_bridge_address)

accounts
  1  $CONFIG  writable
  2  $VAULT  signer (multisig vault PDA)

data (hex)
  $DATA

data (base64)
  $B64

In Squads: New Transaction -> Custom instruction, paste the program id, the two
accounts (the second added as a signer), then the data. Simulate, sign, execute.

Revert by running this again with the previous 64-byte value.
EOF
