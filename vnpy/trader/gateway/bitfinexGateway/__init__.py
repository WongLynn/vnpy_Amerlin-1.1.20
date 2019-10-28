# encoding: UTF-8
"""
需要在先关的底层引擎之中添加Bitfinex


"""
from __future__ import absolute_import
from vnpy.trader import vtConstant
from .bitfinexGateway import BitfinexGateay

gatewayClass = BitfinexGateay
gatewayName = 'BITFINEX'
gatewayDisplayName = u'BITFINEX'
gatewayType = vtConstant.GATEWAYTYPE_BTC
gatewayQryEnabled = True




