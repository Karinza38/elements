#!/usr/bin/env python3
# Copyright (c) 2015-2020 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Utilities for manipulating blocks and transactions."""

from binascii import a2b_hex
import struct
import time
import unittest

from .address import (
    key_to_p2sh_p2wpkh,
    key_to_p2wpkh,
    script_to_p2sh_p2wsh,
    script_to_p2wsh,
)
from .messages import (
    CBlock,
    COIN,
    COutPoint,
    CTransaction,
    CTxIn,
    CTxInWitness,
    CTxOut,
    CTxOutValue,
    hash256,
    hex_str_to_bytes,
    ser_uint256,
    tx_from_hex,
    uint256_from_str,
    CProof,
)
from .script import (
    CScript,
    CScriptNum,
    CScriptOp,
    OP_1,
    OP_CHECKMULTISIG,
    OP_CHECKSIG,
    OP_RETURN,
    OP_TRUE,
)
from .script_util import (
    key_to_p2wpkh_script,
    script_to_p2wsh_script,
)
from .util import assert_equal

WITNESS_SCALE_FACTOR = 4
MAX_BLOCK_SIGOPS = 20000
MAX_BLOCK_SIGOPS_WEIGHT = MAX_BLOCK_SIGOPS * WITNESS_SCALE_FACTOR

# Genesis block time (regtest)
TIME_GENESIS_BLOCK = 1296688602

# Coinbase transaction outputs can only be spent after this number of new blocks (network rule)
COINBASE_MATURITY = 100

# Soft-fork activation heights
CLTV_HEIGHT = 1351
CSV_ACTIVATION_HEIGHT = 432

# From BIP141
WITNESS_COMMITMENT_HEADER = b"\xaa\x21\xa9\xed"

NORMAL_GBT_REQUEST_PARAMS = {"rules": ["segwit"]}

# Assumes a BIP34 valid commitment exists
def get_coinbase_height(coinbase):
    if CScriptOp.is_small_int(coinbase.vin[0].scriptSig[0]):
        return CScriptOp.decode_op_n(coinbase.vin[0].scriptSig[0])
    else:
        return CScriptNum.decode(coinbase.vin[0].scriptSig)


def create_block(hashprev=None, coinbase=None, ntime=None, *, version=None, tmpl=None, txlist=None):
    """Create a block (with regtest difficulty)."""
    block = CBlock()
    if tmpl is None:
        tmpl = {}
    block.nVersion = version or tmpl.get('version') or 1
    block.nTime = ntime or tmpl.get('curtime') or int(time.time() + 600)
    block.hashPrevBlock = hashprev or int(tmpl['previousblockhash'], 0x10)
    if tmpl and not tmpl.get('bits') is None:
        block.nBits = struct.unpack('>I', a2b_hex(tmpl['bits']))[0]
    else:
        block.nBits = 0x207fffff  # difficulty retargeting is disabled in REGTEST chainparams
    if coinbase is None:
        coinbase = create_coinbase(height=tmpl['height'])
    block.vtx.append(coinbase)
    block.proof = CProof(bytearray.fromhex('51'), bytearray.fromhex(''))
    block.block_height = get_coinbase_height(coinbase)
    if txlist:
        for tx in txlist:
            if not hasattr(tx, 'calc_sha256'):
                tx = tx_from_hex(tx)
            block.vtx.append(tx)
    block.hashMerkleRoot = block.calc_merkle_root()
    block.calc_sha256()
    return block

def get_witness_script(witness_root, witness_nonce):
    witness_commitment = uint256_from_str(hash256(ser_uint256(witness_root) + ser_uint256(witness_nonce)))
    output_data = WITNESS_COMMITMENT_HEADER + ser_uint256(witness_commitment)
    return CScript([OP_RETURN, output_data])

def add_witness_commitment(block, nonce=0):
    """Add a witness commitment to the block's coinbase transaction.

    According to BIP141, blocks with witness rules active must commit to the
    hash of all in-block transactions including witness."""
    # First calculate the merkle root of the block's
    # transactions, with witnesses.
    witness_nonce = nonce

    # ELEMENTS: add empty txout to end of coinbase tx
    block.vtx[0].vout.append(CTxOut())
    # block.vtx[0].vout[-1].nAsset.setNull() # TODO find out why this breaks stuff
    # unless you directly put back in a valid .vchCommitment

    witness_root_hex = block.calc_witness_merkle_root()
    witness_root = uint256_from_str(hex_str_to_bytes(witness_root_hex)[::-1])
    # witness_nonce should go to coinbase witness.
    block.vtx[0].wit.vtxinwit = [CTxInWitness()]
    block.vtx[0].wit.vtxinwit[0].scriptWitness.stack = [ser_uint256(witness_nonce)]

    # witness commitment is the last OP_RETURN output in coinbase
    block.vtx[0].vout[-1] = CTxOut(0, get_witness_script(witness_root, witness_nonce))
    block.vtx[0].rehash()
    block.hashMerkleRoot = block.calc_merkle_root()
    block.rehash()


def script_BIP34_coinbase_height(height):
    if height <= 16:
        res = CScriptOp.encode_op_n(height)
        # Append dummy to increase scriptSig size above 2 (see bad-cb-length consensus rule)
        return CScript([res, OP_1])
    return CScript([CScriptNum(height)])


def create_coinbase(height, pubkey=None, extra_output_script=None, fees=0, nValue=50):
    """Create a coinbase transaction.

    If pubkey is passed in, the coinbase output will be a P2PK output;
    otherwise an anyone-can-spend output.

    If extra_output_script is given, make a 0-value output to that
    script. This is useful to pad block weight/sigops as needed. """
    coinbase = CTransaction()
    coinbase.vin.append(CTxIn(COutPoint(0, 0xffffffff), script_BIP34_coinbase_height(height), 0xffffffff))
    coinbaseoutput = CTxOut()
    value = nValue * COIN
    if nValue == 50:
        halvings = int(height / 150)  # regtest
        value >>= halvings
        value += fees
    coinbaseoutput.nValue = CTxOutValue(value)
    if pubkey is not None:
        coinbaseoutput.scriptPubKey = CScript([pubkey, OP_CHECKSIG])
    else:
        coinbaseoutput.scriptPubKey = CScript([OP_TRUE])
    coinbase.vout = [coinbaseoutput]
    if extra_output_script is not None:
        coinbaseoutput2 = CTxOut()
        coinbaseoutput2.nValue = CTxOutValue(0)
        coinbaseoutput2.scriptPubKey = extra_output_script
        coinbase.vout.append(coinbaseoutput2)
    coinbase.calc_sha256()
    return coinbase

def create_tx_with_script(prevtx, n, script_sig=b"", *, amount, fee=0, script_pub_key=CScript()):
    """Return one-input, one-output transaction object
       spending the prevtx's n-th output with the given amount.

       Can optionally pass scriptPubKey and scriptSig, default is anyone-can-spend output.
    """
    tx = CTransaction()
    assert n < len(prevtx.vout)
    tx.vin.append(CTxIn(COutPoint(prevtx.sha256, n), script_sig, 0xffffffff))
    tx.vout.append(CTxOut(amount, script_pub_key))
    if fee > 0:
        tx.vout.append(CTxOut(fee))
    tx.calc_sha256()
    return tx

def create_transaction(node, txid, to_address, *, amount, fee, locktime=0):
    """ Return signed transaction spending the first output of the
        input txid. Note that the node must have a wallet that can
        sign for the output that is being spent.
    """
    raw_tx = create_raw_transaction(node, txid, to_address, amount=amount, fee=fee, locktime=locktime)
    tx = tx_from_hex(raw_tx)
    return tx

def create_raw_transaction(node, txid, to_address, *, amount, fee, locktime=0):
    """ Return raw signed transaction spending the first output of the
        input txid. Note that the node must have a wallet that can sign
        for the output that is being spent.
    """
    psbt = node.createpsbt(inputs=[{"txid": txid, "vout": 0}], outputs=[{to_address: amount}, {"fee": fee}], locktime=locktime)
    for sign in [False, True]:
        for w in node.listwallets():
            wrpc = node.get_wallet_rpc(w)
            psbt = wrpc.walletprocesspsbt(psbt, sign)["psbt"]
    final_psbt = node.finalizepsbt(psbt)
    assert_equal(final_psbt["complete"], True)
    return final_psbt['hex']

def get_legacy_sigopcount_block(block, accurate=True):
    count = 0
    for tx in block.vtx:
        count += get_legacy_sigopcount_tx(tx, accurate)
    return count

def get_legacy_sigopcount_tx(tx, accurate=True):
    count = 0
    for i in tx.vout:
        count += CScript(i.scriptPubKey).GetSigOpCount(accurate)
    for j in tx.vin:
        # scriptSig might be of type bytes, so convert to CScript for the moment
        count += CScript(j.scriptSig).GetSigOpCount(accurate)
    return count

def witness_script(use_p2wsh, pubkey):
    """Create a scriptPubKey for a pay-to-witness TxOut.

    This is either a P2WPKH output for the given pubkey, or a P2WSH output of a
    1-of-1 multisig for the given pubkey. Returns the hex encoding of the
    scriptPubKey."""
    if not use_p2wsh:
        # P2WPKH instead
        pkscript = key_to_p2wpkh_script(pubkey)
    else:
        # 1-of-1 multisig
        witness_script = CScript([OP_1, hex_str_to_bytes(pubkey), OP_1, OP_CHECKMULTISIG])
        pkscript = script_to_p2wsh_script(witness_script)
    return pkscript.hex()

def create_witness_tx(node, use_p2wsh, utxo, pubkey, encode_p2sh, amount):
    """Return a transaction (in hex) that spends the given utxo to a segwit output.

    Optionally wrap the segwit output using P2SH."""
    if use_p2wsh:
        program = CScript([OP_1, hex_str_to_bytes(pubkey), OP_1, OP_CHECKMULTISIG])
        addr = script_to_p2sh_p2wsh(program) if encode_p2sh else script_to_p2wsh(program)
    else:
        addr = key_to_p2sh_p2wpkh(pubkey) if encode_p2sh else key_to_p2wpkh(pubkey)
    if not encode_p2sh:
        assert_equal(node.getaddressinfo(addr)['scriptPubKey'], witness_script(use_p2wsh, pubkey))
    if "amount" not in utxo:
        utxo["amount"] = node.gettxout(utxo["txid"], utxo["vout"])["value"]
    return node.createrawtransaction([utxo], [{addr: amount}, {"fee": utxo["amount"]-amount}])

def send_to_witness(use_p2wsh, node, utxo, pubkey, encode_p2sh, amount, sign=True, insert_redeem_script=""):
    """Create a transaction spending a given utxo to a segwit output.

    The output corresponds to the given pubkey: use_p2wsh determines whether to
    use P2WPKH or P2WSH; encode_p2sh determines whether to wrap in P2SH.
    sign=True will have the given node sign the transaction.
    insert_redeem_script will be added to the scriptSig, if given."""
    tx_to_witness = create_witness_tx(node, use_p2wsh, utxo, pubkey, encode_p2sh, amount)
    if (sign):
        signed = node.signrawtransactionwithwallet(tx_to_witness)
        assert "errors" not in signed or len(["errors"]) == 0
        return node.sendrawtransaction(signed["hex"])
    else:
        if (insert_redeem_script):
            tx = tx_from_hex(tx_to_witness)
            tx.vin[0].scriptSig += CScript([hex_str_to_bytes(insert_redeem_script)])
            tx_to_witness = tx.serialize().hex()

    return node.sendrawtransaction(tx_to_witness)

class TestFrameworkBlockTools(unittest.TestCase):
    def test_create_coinbase(self):
        height = 20
        coinbase_tx = create_coinbase(height=height)
        assert_equal(CScriptNum.decode(coinbase_tx.vin[0].scriptSig), height)
