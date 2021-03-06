#! /usr/bin/python3

"""Pay out dividends."""

import struct
import logging
import decimal
D = decimal.Decimal

from . import (util, config, exceptions, bitcoin, util)

FORMAT = '>QQ'
ID = 50
LENGTH = 8 + 8

def create (db, source, amount_per_share, asset, test=False):
    if asset in ('BTC', 'XCP'):
        raise exceptions.DividendError('Cannot send dividends to BTC or XCP.')

    issuances = util.get_issuances(db, validity='Valid', asset=asset)
    if not issuances: raise exceptions.AssetError('No such asset: {}'.format(asset))
    divisible = issuances[0]['divisible']
    if divisible:
        total_shares = sum([issuance['amount'] for issuance in issuances]) / config.UNIT
    else:
        total_shares = sum([issuance['amount'] for issuance in issuances])
    amount = amount_per_share * total_shares

    balances = util.get_balances(db, address=source, asset='XCP')
    if not balances or balances[0]['amount'] < amount:
        raise exceptions.BalanceError('Insufficient funds. (Check that the database is up-to-date.)')
    if not issuances:
        raise exceptions.DividendError('No such asset: {}.'.format(asset))
    if not amount_per_share:
        raise exceptions.UselessError('Zero amount per share.')
    print('Total amount to be distributed in dividends:', amount / config.UNIT)
    asset_id = util.get_asset_id(asset)
    data = config.PREFIX + struct.pack(config.TXTYPE_FORMAT, ID)
    data += struct.pack(FORMAT, amount_per_share, asset_id)
    return bitcoin.transaction(source, None, None, config.MIN_FEE, data, test)

def parse (db, tx, message):
    dividend_parse_cursor = db.cursor()
    validity = 'Valid'

    # Unpack message.
    try:
        amount_per_share, asset_id = struct.unpack(FORMAT, message)
        asset = util.get_asset_name(asset_id)
    except Exception:
        amount_per_share, asset = None, None
        validity = 'Invalid: could not unpack'

    # For SQLite3
    amount_per_share = min(amount_per_share, config.MAX_INT)

    if validity == 'Valid':
        if not amount_per_share:
            validity = 'Invalid: zero amount per share.'

    if validity == 'Valid':
        if asset in ('BTC', 'XCP'):
            validity = 'Invalid: cannot send dividends to BTC or XCP'
        elif not util.valid_asset_name(asset):
            validity = 'Invalid: bad Asset ID'

    # Debit.
    if validity == 'Valid':
        issuances = util.get_issuances(db, validity='Valid', asset=asset)
        total_shares = sum([issuance['amount'] for issuance in issuances])
        amount = amount_per_share * round(D(total_shares) / config.UNIT)
        if amount:
            validity = util.debit(db, tx['source'], 'XCP', amount)
        else: validity = 'Invalid: dividend too small'

    # Credit.
    if validity == 'Valid':
        balances = util.get_balances(db, asset=asset)
        for balance in balances:
            address, address_amount = balance['address'], balance['amount']
            address_amount = round(D(address_amount) / config.UNIT)
            amount = address_amount * amount_per_share
            util.credit(db, address, 'XCP', amount)

    # Add parsed transaction to message-type–specific table.
    element_data = {
        'tx_index': tx['tx_index'],
        'tx_hash': tx['tx_hash'],
        'block_index': tx['block_index'],
        'source': tx['source'],
        'asset': asset,
        'amount_per_share': amount_per_share,
        'validity': validity,
    }
    dividend_parse_cursor.execute(*util.get_insert_sql('dividends', element_data))
    config.zeromq_publisher.push_to_subscribers('new_dividend', element_data)
    if validity == 'Valid':
        logging.info('Dividend: {} paid {} per share of asset {} ({})'.format(tx['source'], util.devise(db, amount_per_share, 'XCP', 'output'), asset, util.short(tx['tx_hash'])))

    dividend_parse_cursor.close()

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
