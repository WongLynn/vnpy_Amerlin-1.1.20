# encoding: UTF-8
'''
vnpy.api.bitfinex的gateway接入
改进版的bitfinex 加载历史记录
处理并且添加inintposition 用于实盘之中停止之后获取策略的仓位，即策略的仓位的维护作用
添加margin 账号数据，注意这里常规变量的引入contast 
'''

from __future__ import print_function
import json
import hashlib
import hmac
from copy import copy
from math import pow
import requests
import pandas as pd
from time import sleep
from datetime import datetime, timedelta
from pandas import Series, DataFrame
import time
import os
import numpy as np


from vnpy.api.bitfinex import BitfinexApi
from vnpy.trader.vtGateway import *
from vnpy.trader.vtFunction import getJsonPath, getTempPath
from vnpy.trader.vtObject import *
from vnpy.trader.app.ctaStrategy.ctaBase import EVENT_CTA_LOG
from vnpy.trader.app.ctaStrategy.ctaBase import *
from vnpy.trader.vtConstant import *
from vnpy.trader.language import constant


# 使用tabulte 对显示进行细节控制
from tabulate import tabulate
tabulate.PRESERVE_WHITESPACE = True
pd.set_option('expand_frame_repr', False)                          # 当列太多时不换行

# 这里使用四种状态类型，对交易状态进行返回
statusMapReverse = {}
statusMapReverse['ACTIVE'] = constant.STATUS_NOTTRADED                      # pending 订单活跃状态中
statusMapReverse['PARTIALLYFILLED'] = constant.STATUS_PARTTRADED            # 'partial filled' 部分交易
statusMapReverse['EXECUTED'] = constant.STATUS_ALLTRADED                    # 'filled'   交易完成
statusMapReverse['CANCELED'] = constant.STATUS_CANCELLED                    # 'cancelled'  已经全部取消

"""
Order Status: ACTIVE,
              PARTIALLY FILLED @ PRICE(AMOUNT),
              EXECUTED @ PRICE(AMOUNT) e.g. "EXECUTED @ 107.6(-0.2)",
              CANCELED,
              INSUFFICIENT MARGIN was: PARTIALLY FILLED @ PRICE(AMOUNT),
              CANCELED was: PARTIALLY FILLED @ PRICE(AMOUNT)

"""

#价格类型映射 这里有三种价格类型
priceTypeMap = {}
priceTypeMap[constant.PRICETYPE_LIMITPRICE] = 'LIMIT'
priceTypeMap[constant.PRICETYPE_MARKETPRICE] = 'MARKET'
priceTypeMap[constant.PRICETYPE_FOK] = 'FILL-OR-KILL'


# 使用ccxt 对交易细节进行控制
import ccxt
bitfinex = ccxt.bitfinex2()                        #因为bitfinex 在ccxt之中显示这里使用bitfinex2

# import pdb;pdb.set_trace()

############################################################################################
class BitfinexGateay(VtGateway):                                               #网关接口定义类函数
    """Bitfinex接口"""

    # ----------------------------------------------------------------------
    def __init__(self, eventEngine, gatewayName=''):
        """Constructor"""

        super(BitfinexGateay, self).__init__(eventEngine, gatewayName)
        self.api = GatewayApi(self)                                           # vnapi 类继承函数

        self.qryEnabled = False                                               # 是否要启动循环查询

        self.fileName = self.gatewayName + '_connect.json'
        self.filePath = getJsonPath(self.fileName, __file__)

        self.connected = False
        self.count = 0

    # ----------------------------------------------------------------------
    def connect(self):
        """连接"""
        # 如果 accessKey accessSec pairs 在初始化的时候已经设置了，则不用配置文件里的了
        try:
            f = open(self.filePath)
        except IOError:
            log = VtLogData()
            log.gatewayName = self.gatewayName
            log.logContent = u'读取连接配置出错，请检查'
            self.onLog(log)
            return
        # 解析json文件
        setting = json.load(f)
        f.close()
        try:
            apiKey = str(setting['apiKey'])
            secretKey = str(setting['secretKey'])
            symbols = setting['symbols']
        except KeyError:
            log = VtLogData()
            log.gatewayName = self.gatewayName
            log.logContent = u'连接配置缺少字段，请检查'
            self.onLog(log)
            return

        # 如果已经处于连接状态，则直接返回
        if self.connected:
            return

        # 创建行情和交易接口对象
        self.api.connect(apiKey, secretKey, symbols)
        self.connected = True

        # 创建是否进行查询属性
        setQryEnabled = setting.get('setQryEnabled', None)
        self.setQryEnabled(setQryEnabled)

        # 创建查询频率
        setQryFreq = setting.get('setQryFreq', 60)
        self.initQuery(setQryFreq)

    # ----------------------------------------------------------------------
    def subscribe(self, subscribeReq):
        """订阅行情"""
        pass

    # ----------------------------------------------------------------------
    def sendOrder(self, orderReq):
        """发单"""
        return self.api.sendOrder(orderReq)

    # ----------------------------------------------------------------------
    def cancelOrder(self, cancelOrderReq):
        """撤单"""
        self.api.cancelOrder(cancelOrderReq)

    # ----------------------------------------------------------------------
    def close(self):
        """关闭"""
        self.api.close()

    # -------------------------------------------------------------------
    def qryPosition(self):
        """查询持仓"""
        self.api.onPosition()

    # ----------------------------------------------------------------------
    def qryAccount(self):
        """查询账户"""
        self.api.onWallet()

    # ----------------------------------------------------------------------
    def initQuery(self, freq=60):
        """初始化连续查询"""
        if self.qryEnabled:
            # 需要循环的查询函数列表
            self.qryFunctionList = [self.queryInfo]

            self.qryCount = 0                   # 查询触发倒计时
            self.qryTrigger = freq              # 查询触发点
            self.qryNextFunction = 0            # 上次运行的查询函数索引

            self.startQuery()

    # ----------------------------------------------------------------------
    def query(self, event):
        """注册到事件处理引擎上的查询函数"""
        self.qryCount += 1

        if self.qryCount > self.qryTrigger:
            # 清空倒计时
            self.qryCount = 0

            # 执行查询函数
            function = self.qryFunctionList[self.qryNextFunction]
            function()

            # 计算下次查询函数的索引，如果超过了列表长度，则重新设为0
            self.qryNextFunction += 1
            if self.qryNextFunction == len(self.qryFunctionList):
                self.qryNextFunction = 0

    # ----------------------------------------------------------------------
    def startQuery(self):
        """启动连续查询"""
        self.eventEngine.register(EVENT_TIMER, self.query)

    # ----------------------------------------------------------------------
    def setQryEnabled(self, qryEnabled):
        """设置是否要启动循环查询"""
        self.qryEnabled = qryEnabled

    # ----------------------------------------------------------------------
    def queryInfo(self):
        """"""
        self.api.queryAccount()
        self.api.queryPosition()

    # ----------------------------------------------------------------------
    # 策略启动初始化持仓，在策略在持仓期间停止重启时候进行持仓信息的查询，这里使用restful 接口进行查
    def initPosition(self, vtSymbol):
        # print("策略启动，仓位initposition初始化")
        self.api.queryPosition()

    def qryAllOrders(self, vtSymbol, order_id, status=None):
        pass

    def loadHistoryBar(self, vtSymbol, type_, size=None, since=None):

        """
        注意映射的周期后边的周期进行映射  针对bitfinex 有以下周期 ，使用ctaenging 模板在实盘期间进行数据的下载；
        注意这里的时间周期的转换映射，下面的时间周期是bitfinex 时间周期
        '1m', '5m', '15m', '30m', '1h', '3h', '6h', '12h', '1D', '7D', '14D', '1M'
        """
        symbol = vtSymbol.split(':')[0]
        typeMap = {}                                                       #vnpy 引擎支持的时间周期转换
        typeMap['1min'] = '1m'
        typeMap['5min'] = '5m'
        typeMap['15min'] = '15m'
        typeMap['30min'] = '30m'
        typeMap['60min'] = '1h'
        typeMap['360min'] = '6h'
        url = f'https://api.bitfinex.com/v2/candles/trade:{typeMap[type_]}:t{symbol}/hist'
        params = {}
        if size:
            params['limit'] = size
        if since:
            params['start'] = since
        r = requests.get(url, headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'
        }, params=params, timeout=10)

        df = pd.DataFrame(r.json(), columns=["MTS", "open", "close", "high", "low", "volume"])
        """
             MTS      open     close      high     low        volume
        0    1556763900000  4.931400  4.931100  4.931400  4.9311    110.262000
        1    1556763600000  4.931700  4.933000  4.933000  4.9296    858.350710
        """
        df["datetime"] = df["MTS"].map(lambda x: datetime.fromtimestamp(x / 1000))
        df['volume'] = df['volume'].map(lambda x: float(x))
        df['open'] = df['open'].map(lambda x: float(x))
        df['high'] = df['high'].map(lambda x: float(x))
        df['low'] = df['low'].map(lambda x: float(x))
        df['close'] = df['close'].map(lambda x: float(x))
        pm = df.sort_values(by="datetime", ascending=True)                 # 对时间以及数据进行转换
        # print("pm", pm)
        return pm


#########################################################################################
class GatewayApi(BitfinexApi):
    """API实现"""

    def __init__(self, gateway):
        """Constructor"""
        super(GatewayApi, self).__init__()

        self.gateway = gateway                          # gateway对象
        self.gatewayName = gateway.gatewayName          # gateway对象名称
        self.symbols = []

        # 根据其中的api 接口这里传入的是utc 标准时间格式数组
        self.orderId = 1
        self.date = int(datetime.now().timestamp()) * self.orderId

        self.currencys = []
        self.tickDict = {}
        self.bidDict = {}
        self.askDict = {}
        self.orderLocalDict = {}                          # 维护的本地订单编号字典
        self.channelDict = {}                             # ChannelID : (Channel, Symbol)

        self.accountDict = {}                             # 在定义account 账号时候使用

        """
        关键要点，因为在bitfinex api 之中没有order的 "开仓"、"平仓"的属性，也就是说，仅有多头（开仓、平空）以及空头（开仓、平多）
        根据bitfinex 针对position 持仓以及 trade 返回的时间顺序，这里使用仓位进行判断，首先定义position 为 None
        1)当判断order交易信息为>0 则认为其为"开多"
        
        """
        # self.direction = DIRECTION_NET                     # 默认方向为空方向，在初始化时候ps 时候，定义为none
        self.direction = constant.DIRECTION_NET              # 默认方向为空方向，在初始化时候ps 时候，定义为none
    # ----------------------------------------------------------------------
    def connect(self, apiKey, secretKey, symbols):
        """连接服务器"""
        self.apiKey = apiKey
        self.secretKey = secretKey
        self.symbols = symbols

        self.start()
        self.writeLog(u'bitfinex 交易API启动成功')

    # ----------------------------------------------------------------------
    def onConnect(self):
        """"""
        for symbol in self.symbols:
            self.subscribe(symbol, 'ticker')
            self.subscribe(symbol, 'book')
        self.writeLog(u'bitfinex 行情推送订阅成功')

        # 只获取数据，不交易
        self.authenticate()
        self.writeLog(u'bitfinex 账户认证成功')

        self.sendRestReq('/symbols_details', self.onSymbolDetails, post=False)

    # ----------------------------------------------------------------------
    def subscribe(self, symbol, channel):
        """"""
        if not symbol.startswith("t"):
            symbol = "t" + symbol

        req = {
            'event': 'subscribe',
            'channel': channel,
            'symbol': symbol
        }
        self.sendReq(req)                                        #使用ws进行连续查询

    # ----------------------------------------------------------------------
    def authenticate(self):
        """"""
        nonce = int(time.time() * 1000000)
        authPayload = 'AUTH' + str(nonce)
        signature = hmac.new(
            self.secretKey.encode(),
            msg=authPayload.encode(),
            digestmod=hashlib.sha384
        ).hexdigest()

        req = {
            'apiKey': self.apiKey,
            'event': 'auth',
            'authPayload': authPayload,
            'authNonce': nonce,
            'authSig': signature
        }

        self.sendReq(req)                                         #使用ws进行连续查询

    # ----------------------------------------------------------------------
    def writeLog(self, content):
        """发出日志"""
        log = VtLogData()
        log.gatewayName = self.gatewayName
        log.logContent = content
        self.gateway.onLog(log)

    # ----------------------------------------------------------------------
    def generateDateTime(self, s):
        """生成时间"""
        dt = datetime.fromtimestamp(s / 1000.0)
        date = dt.strftime('%Y-%m-%d')
        time = dt.strftime("%H:%M:%S.%f")
        return date, time

    def sendOrder(self, orderReq):
        """
        # vnpy 框架底层引擎为 策略---策略模板---策略引擎--主引擎---gateway 所以要知道其中对应的关系,根据vnpy 框架模板，
        目前从事件驱动以及主引擎传递到gateway 的volume 都是 正数，需要根据传递过来的orderReq的结构体进行发单，发单包括
        两个维度"开"、"平"  以及 "多头"、"空头"； 【vnpy 系统传递交易的数量volume】----》【gateway 以及原生的bitfinex
        的接口函数的数量的 amount的传递】
        buy ---- sell
        long  open /   short close
        short ---cover
        short open /  long  close
        :param orderReq:
        :return:
        """
        # print('gateway senderorder orderReq._dict_', orderReq.__dict__)
        amount = 0                                      # 在引入amount 之前定义amount变量，实际罚单之中的数量
        self.orderId += 1
        orderId = self.date + self.orderId
        vtOrderID = ':'.join([self.gatewayName, str(orderId)])                            #本地维护的订单编号

        # 注意对amount 的定义，因为监听传过来有四种组合
        if orderReq.direction == constant.DIRECTION_LONG and orderReq.offset == constant.OFFSET_OPEN:        # 买开  buy
            amount = orderReq.volume
        elif orderReq.direction == constant.DIRECTION_SHORT and orderReq.offset == constant.OFFSET_OPEN:     # 卖开  short
            amount = -orderReq.volume
        elif orderReq.direction == constant.DIRECTION_SHORT and orderReq.offset == constant.OFFSET_CLOSE:    # 卖平  sell
            amount = -orderReq.volume
        elif orderReq.direction == constant.DIRECTION_LONG and orderReq.offset == constant.OFFSET_CLOSE:     # 买平   cover
            amount = orderReq.volume
        """
        注意原生的所有的bitfinex交易api 的接入的数据的数据类型以及数据的格式，特别是symbol 接入的格式
        """

        oSymbol = orderReq.symbol
        if not oSymbol.startswith("t"):
            oSymbol = "t" + oSymbol

        o = {
            'cid': orderId,                        # Should be unique in the day (UTC) (not enforced)  int45
            'type': priceTypeMap[orderReq.priceType],
            'symbol': oSymbol,
            'amount': str(amount),
            'price': str(orderReq.price)
        }

        req = [0, 'on', None, o]
        self.sendReq(req)
        return vtOrderID

    # ----------------------------------------------------------------------
    def cancelOrder(self, cancelOrderReq):
        """"""
        orderId = int(cancelOrderReq.orderID)
        date = cancelOrderReq.sessionID

        req = [
            0,
            'oc',
            None,
            {
                'cid': orderId,
                'cid_date': date,
            }
        ]

        self.sendReq(req)

    # ----------------------------------------------------------------------
    def calc(self):
        """"""
        l = []
        for currency in self.currencys:
            l.append(['wallet_exchange_' + currency])

        req = [0, 'calc', None, l]
        self.sendReq(req)

    # ----------------------------------------------------------------------
    def onData(self, data):
        """
        根据ws 协议，数据流不断地流动过来，根据数据的不同的类型继续进行判定
        :param data:
        :return:
        """

        if isinstance(data, dict):
            self.onResponse(data)
        else:
            self.onUpdate(data)

    # ----------------------------------------------------------------------
    def printData(self, data):
        self.writeLog(u'数据连接正常，解析成功 %s' % (data))

    # ----------------------------------------------------------------------
    def onResponse(self, data):
        """"""
        if 'event' not in data:
            return

        # 如果有错误的返回信息，要打印出来
        #print("[onResponse]data:" + json.dumps(data))

        if data['event'] == 'subscribed':
            symbol = str(data['symbol'].replace('t', ''))
            self.channelDict[data['chanId']] = (data['channel'], symbol)

    # ----------------------------------------------------------------------
    def onUpdate(self, data):
        """"""
        if data[1] == u'hb':
            return

        channelID = data[0]

        if not channelID:
            self.onTradeUpdate(data)
        else:
            self.onDataUpdate(data)

    # ----------------------------------------------------------------------
    def onDataUpdate(self, data):
        """
        该函数用于不断地更新bitfinex 的行情推送
        :param data:
        :return:
        """
        channelID = data[0]
        channel, symbol = self.channelDict[channelID]
        symbol = str(symbol.replace('t', ''))

        # 获取Tick对象
        if symbol in self.tickDict:
            tick = self.tickDict[symbol]
        else:
            tick = VtTickData()
            tick.gatewayName = self.gatewayName
            tick.symbol = symbol
            tick.exchange = constant.EXCHANGE_BITFINEX
            tick.vtSymbol = ':'.join([tick.symbol, tick.exchange])
            self.tickDict[symbol] = tick

        l = data[1]

        # 常规行情更新
        if channel == 'ticker':
            tick.volume = float(l[-3])
            tick.highPrice = float(l[-2])
            tick.lowPrice = float(l[-1])
            tick.lastPrice = float(l[-4])
            tick.openPrice = float(tick.lastPrice - l[4])
        # 深度报价更新
        elif channel == 'book':
            bid = self.bidDict.setdefault(symbol, {})
            ask = self.askDict.setdefault(symbol, {})

            if len(l) > 3:
                for price, count, amount in l:
                    price = float(price)
                    count = int(count)
                    amount = float(amount)

                    if amount > 0:
                        bid[price] = amount
                    else:
                        ask[price] = -amount
            else:
                price, count, amount = l
                price = float(price)
                count = int(count)
                amount = float(amount)

                if not count:
                    if price in bid:
                        del bid[price]
                    elif price in ask:
                        del ask[price]
                else:
                    if amount > 0:
                        bid[price] = amount
                    else:
                        ask[price] = -amount
            """
            Bitfinex的深度数据更新是逐档推送变动情况，而非5档一起推
            因此会出现没有Bid或者Ask的情况，这里使用try...catch过滤
            只有买卖深度满足5档时才做推送
            
            """
            try:
                # BID
                bidPriceList = bid.keys()
                bidPriceList = sorted(bidPriceList)

                tick.bidPrice1 = bidPriceList[0]
                tick.bidPrice2 = bidPriceList[1]
                tick.bidPrice3 = bidPriceList[2]
                tick.bidPrice4 = bidPriceList[3]
                tick.bidPrice5 = bidPriceList[4]

                tick.bidVolume1 = bid[tick.bidPrice1]
                tick.bidVolume2 = bid[tick.bidPrice2]
                tick.bidVolume3 = bid[tick.bidPrice3]
                tick.bidVolume4 = bid[tick.bidPrice4]
                tick.bidVolume5 = bid[tick.bidPrice5]

                # ASK
                askPriceList = ask.keys()
                askPriceList = sorted(askPriceList)

                tick.askPrice1 = askPriceList[0]
                tick.askPrice2 = askPriceList[1]
                tick.askPrice3 = askPriceList[2]
                tick.askPrice4 = askPriceList[3]
                tick.askPrice5 = askPriceList[4]

                tick.askVolume1 = ask[tick.askPrice1]
                tick.askVolume2 = ask[tick.askPrice2]
                tick.askVolume3 = ask[tick.askPrice3]
                tick.askVolume4 = ask[tick.askPrice4]
                tick.askVolume5 = ask[tick.askPrice5]
            except IndexError:
                return

        dt = datetime.now()
        tick.date = dt.strftime('%Y%m%d')
        tick.time = dt.strftime('%H:%M:%S.%f')
        tick.datetime = dt

        # 推送
        self.gateway.onTick(copy(tick))

    # ----------------------------------------------------------------------
    def onTradeUpdate(self, data):
        """
        ws数据反馈，这里注意在bitfinex api 之中ws 数据之中的数据流的先后顺序
        :param data:
        :return:
        """
        name = data[1]
        info = data[2]
        #-------------------------------order 活动委托更新状态--------------------------------
        if name == 'os':                                             # orders活动委托，发单委托
            for l in info:
                self.onOrder(l)
            self.writeLog(u' api 订单委托【快照】orders获取成功')
        elif name in ['on', 'ou', 'oc']:                             # orders活动委托，发单更新
            self.onOrder(info)
            self.writeLog(u' api 订单委托【更新】orders获取成功')

        # -------------------------------trade 委托更新状态--------------------------------
        elif name == 'te':
            self.onTrade(info)
            self.writeLog(u' api 活动委托【快照】trades 获取成功')
        # elif name == 'tu':                                         # tradeupdates
        #     # 接下来更新到的是'tu'  排序4
        #     self.onTrade(info)

        # --------------position 更新状态  该仓位指交易主界面UI的合约的仓位------------
        elif name == 'ps':
            for l in info:
                self.onPosition(l)
                self.writeLog(u'api 仓位初始化【快照】positon 获取成功')
        elif name in ['pn', 'pu', 'pc']:
            """
            这里获取的每一个资金账户之中的每一个币种，并且一一列举出来，包含利润，杠杆等信息
            这里对仓位进行细粒度的控制，因为仓位的放行决定了 order 的方向
            [0, 'ps', [['tEOSUSD', 'ACTIVE', -26.369349,            2.8374,              -4.511e-05, 0, None, None, None, None]
            [0, 'pn', ['tEOSUSD', 'ACTIVE', -6, 4.9154, 0, 0, None, None, None, None, None, None, None, None, None, 0, None, None, None, None]]
            [0, 'pc', ['tEOSUSD', 'CLOSED', 0, 4.9, 0, 0, None, None, None, None, None, None, None, None, None, 0, None, None, None, None]]
            [0, 'pu', ['tEOSUSD',    'ACTIVE', -26.369349,  2.8374,    -5.205e-05,        0,                6.03048553,         8.05994925,  3.32558392,  -2.4796]]
            """
            self.onPosition(info)
            self.writeLog(u'api 持仓信息【更新】positon 获取成功')
        # --------------账户 更新状态 指账户资金情况 -----------------------------------------
        elif name == 'ws':
            for l in info:
                self.onWallet(l)
            self.writeLog(u'账户资金获取成功 【快照】 wallets')
        elif name == 'wu':
            # wallets 账户信息仅包含usd 信息   [0, 'wu', ['margin', 'USD', 213.06576039, 0, None]]
            self.onWallet(info)
            self.writeLog(u'账户资金 usd 【更新】获取成功 wallets')

    # 这里对所有的账号资金进行解析 因为这里是使用margin 进行交易 获取账号信息
    def onWallet(self, data):
        if str(data[0]) == 'margin':
            """
             ['margin', 'USD', 213.11012559, 0, None]
             ['margin', 'ETC', 0.00079896, 0, None]
             ['margin', 'ETH', 0.00457842, 0, None]

            """
            account = VtAccountData()
            account.vtAccountID = self.gatewayName
            account.gatewayName = self.gatewayName
            account.accountID = str(data[1])                               # 交易的币种
            account.vtAccountID = ':'.join([account.gatewayName, account.accountID])
            account.balance = float(data[2])                               # 现有的数量
            if data[-1]:
                account.available = float(data[-1])

            self.gateway.onAccount(account)

    # 这里对所有的持仓的信息进行解析 包含所持有的币种以及币种的方向，使用position 辅助我们进行判定
    def onPosition(self, data):
        """
        1）程序启动，没有仓位，此时ps（持仓初始化快照） 没有返回信息，此时我们初始化定义  self.direction = None
        2) 当交易完成之后，有ps 快照信息，根据上一次快照信息，再进行下一次order 之前判断开平方向
        [0, 'ps', [['tEOSUSD', 'ACTIVE', 6, 4.9, 0, 0, None, None, None, None, None, None, None, None, None, 0, None, None, None, None]]]
        3) 这里在进行平仓动作之后，我们将仓位默认为none 这里的维护我们放在 ctaenging 之中，注意对引擎的改造
        :param data:
        :return:
        """
        pos = VtPositionData()

        Symbol = data[0].split('t')[-1]
        pos.symbol = Symbol
        pos.gatewayName = self.gatewayName
        pos.exchange = constant.EXCHANGE_BITFINEX
        pos.vtSymbol = ':'.join([pos.symbol, pos.exchange])                                  # 合约在vt系统中的唯一代码，合约代码:交易所代码

        # 根据position 快照信息，在 active 状态下进行计算目前的持仓的方向，在bitfinex  api 之中所有的交易信息都是有正负之分的
        pos.position = abs(data[2])                                                         # 这里取持仓量是绝对值
        if data[2] > 0:
            pos.direction = constant.DIRECTION_LONG                                                  # 定义多头仓位
        elif data[2] < 0:
            pos.direction = constant.DIRECTION_SHORT                                                 # 定义空头仓位
        else:
            pos.direction = constant.DIRECTION_NET

        # 这里定义了个全局变量，持仓的方向，方便后期进行引用更新
        self.direction = pos.direction

        pos.vtPositionName = ':'.join([pos.vtSymbol, pos.direction])
        pos.frozen = 0                                                      # 期货没有冻结概念，会直接反向开仓
        pos.price = data[3]                                                 # 持仓均价
        if data[6]:                                                         # 持仓盈亏
            pos.positionProfit = data[6]
        self.gateway.onPosition(pos)

    # ----------------------------------------------------------------------
    def queryPosition(self):
        """
        这里的position是由引擎进行维护的，进行连续查询，在程序启动之后，进行查询，查询的时间间隔是1分钟，查询结果同样在主交易界面UI上面
        :return:
        """
        self.sendRestReq('/positions', self.onQueryPosition, post=True)

        self.writeLog(u"bitfinex api 启用连续查询 仓位position初始化")


    # ----------------------------------------------------------------------
    def queryAccount(self):
        """
        这里的accout 是由引擎进行维护的，主要查询USD值，在程序启动之后，进行查询，查询的时间间隔是1分钟，查询结果同样在主交易界面UI上面
        :return:
        """
        self.sendRestReq('/margin_infos', self.onQueryAccount, post=True)
        self.writeLog(u"bitfinex api 启用连续查询 仓位margin USD 账户初始化")
        self.sendRestReq('/margin_infos', self.printData, post=True)


    def onOrder(self, data):
        """
        所有bitfinex api 之中返回的数据流都是有正负之分的； 监控的是发单的信息
        ontradeupdate [0, 'os', []]       初始化启动为空
        [0, 'oc', [24705433913, None, 1556815903199, 'tEOSUSD', 1556815903199, 1556815903273, 0, -6, 'MARKET',   订单发出只有进行更新
            [23689701610, None, 1554194135, 'tEOSUSD', 1554194460237, 1554194460254, 0, 7, 'LIMIT', None, None, None, 0,
           'EXECUTED @ 4.6351(7.0)', None, None, 4.7, 4.6351, 0, 0, None, None, None, 0, 0, None, None, None, 'API>BFX',
            None, None, None]
            # 这里的order 只有正负之分，没有代表是 多仓买入（买开）    还是空仓买入（卖平），这里还是需要根据之前的pos 的状态进行判定

        :param data:
        :return:

        """
        order = VtOrderData()
        order.gatewayName = self.gatewayName

        order.symbol = str(data[3].replace('t', ''))                                               # 交易对 EOSUSD
        order.exchange = constant.EXCHANGE_BITFINEX                                                         # 交易对 BITFINEX
        order.vtSymbol = ':'.join([order.symbol, order.exchange])                                  # vnpy 系统编号 EOSUSD:BITFINEX

        order.orderID = str(data[2])                                                            # 交易对 1553115420502   交易所返回的client订单编号
        order.vtOrderID = ':'.join([order.gatewayName, order.orderID])                          # vnpy 系统编号 BITFINEX:1553115420502
        order.priceType = str(data[8])                                                          # 价格类型

        # 当之前没有仓位的状态下
        """
        这里做了特殊的处理，因为在bitfinex 之中ws 数据流之中是没有 方向 之区分的，所以我们可以根据返回的数据以及之前定义的self.position 进行
        重新定义order 的方向以及开、平，通过引擎进行维护
        """
        # ----------------------------------------之前没有仓位，即仓位为0，那么 买开 或者  卖开
        if self.direction == constant.DIRECTION_NET:
            if data[7] > 0:
                self.writeLog(u'之前仓位为0，买开，【long,open】')
                order.direction = constant.DIRECTION_LONG
                order.offset = constant.OFFSET_OPEN
            else:
                self.writeLog(u'之前仓位为0，卖开,【short,open】')
                order.direction = constant.DIRECTION_SHORT
                order.offset = constant.OFFSET_OPEN

        # ----------------------------------------之前有多头仓位
        elif self.direction == constant.DIRECTION_LONG:
            if data[7] > 0:
                self.writeLog(u'之前持有多头仓位，买开加仓，【long open】')
                order.direction = constant.DIRECTION_LONG
                order.offset = constant.OFFSET_OPEN
            else:
                self.writeLog(u'之前持有多头仓位，卖出平仓，【short close】')
                order.direction = constant.DIRECTION_SHORT
                order.offset = constant.OFFSET_CLOSE
        # ----------------------------------------之前有空头仓位
        elif self.direction == constant.DIRECTION_SHORT:
            if data[7] > 0:
                self.writeLog(u'之前持有空头仓位，平空减仓')
                order.direction = constant.DIRECTION_LONG
                order.offset = constant.OFFSET_CLOSE
            else:
                print('之前持有空头仓位，做空加仓')
                self.writeLog(u'之前持有空头仓位，做空加仓')
                order.direction = constant.DIRECTION_SHORT
                order.offset = constant.OFFSET_OPEN

        order.price = float(data[16])                                                         # 价格
        """
        根据后边对其中的持仓的处理，首先推送到是position 为了避免冲突所以，这里全部置为0； 同样这里定义一个最新的变量在引擎之中使用
        """
        # 对引擎进行修改这按照实际数据填写
        order.totalVolume = data[7]                                     #有正负之分  发单交易量
        order.tradedVolume = data[7] - data[6]                          #假定完全成交
        order.thisTradedVolume = 0
        order.signalTradedVolume = abs(data[7]) - abs(data[6])          # 这里定义一个新的变量作为策略之中的判定使用

        # 在非完全成交状态下的判断,目前映射状态有很多
        if str(data[13]) == 'INSUFFICIENT BALANCE (U1)' or str(
                data[13]) == 'INSUFFICIENTBALANCE(G1)was:PARTIALLYFILLED':

            order.status = constant.STATUS_UNKNOWN                                            # 状态为 未知
            self.writeLog(u'资金量不足')
        else:
            orderStatus = str(data[13].split('@')[0])
            orderStatus = orderStatus.replace(' ', '')
            order.status = statusMapReverse[orderStatus]                            # 对应的映射为STATUS_ALLTRADED    完全成交

        order.sessionID, order.orderTime = self.generateDateTime(data[4])            # 订单创建时间
        if order.status == constant.STATUS_CANCELLED:
            buf, order.cancelTime = self.generateDateTime(data[5])

        # 我们都在维护这一个本地的字典，本地的订单编号为，key 为ID即order 编号，此标号为trade   values 为订单cid 即我们传入的cid
        self.orderLocalDict[data[0]] = order.orderID
        self.gateway.onOrder(order)
        self.calc()

    # ----------------------------------------------------------------------
    def onTrade(self, data):

        """
        没有order 情况下是不更新trade 的，当有order 的情况下才更新；也就是说order 返回信息与trade 返回信息可以发生关联
        ontradeupdate [0, 'te', [353626766, 'tEOSUSD', 1556815903268, 24705433913, -6, 4.9154, 'MARKET', 4.9133, -1, None, None, 1556815903199]]
        # 根据成交状态可以继续您细粒度的控制 根据成交的状态进行
        ID,             SYMBOL,    MTS_CREATE,   ORDER_ID,  EXEC_AMOUNT,    EXEC_PRICE,    ORDER_TYPE,    ORDER_PRICE,    MAKER,           (MTS_CREATE_order)
        [353626766, 'tEOSUSD', 1556815903268, 24705433913,   -6,             4.9154,         'MARKET',       4.9133,        -1,  None, None, 1556815903199]

        :param data:
        :return:
        """
        trade = VtTradeData()
        trade.gatewayName = self.gatewayName

        trade.symbol = data[1].replace('t', '')                                             #在进行接口开发之中要注意，返回原始数据的格式
        trade.exchange = constant.EXCHANGE_BITFINEX
        trade.vtSymbol = ':'.join([trade.symbol, trade.exchange])

        bitfinex_id = self.orderLocalDict.get(data[3], None)                                #在订单发起时候有order_id，这里是对应的trade 信息
        if not bitfinex_id:
            self.orderLocalDict[data[3]] = data[11]                                         #如果没有本地本地还没有发单
        trade.orderID = self.orderLocalDict[data[3]]

        trade.vtOrderID = ':'.join([trade.gatewayName, str(trade.orderID)])
        # 注意返回值之中的第一个是trade 的编号id,这里需要是str
        trade.tradeID = str(data[0])                                                        #trade 交易的id 是data[0]
        trade.vtTradeID = ':'.join([trade.gatewayName, trade.tradeID])
        '''
        因为trade 返回只有成交的数量，没有成交的方向，所以可以根据仓位来进行判定，思路与order 是一致的；
        这里的trade 还是很有必要的，因为在部分的策略之中，是根据trade 的方向进行开仓与加仓的仓位的 价格的变化的，比如海龟交易策略
        '''
        # 如果成交数量 > 0 ，则分为3种情况，当仓位为多的时候，为"买多（加仓）"；当仓位为空时候，为"买多平仓"；当仓位为0的时候，为"买开"；默认都是一次性全部成交
        if data[4] > 0 and self.direction == constant.DIRECTION_LONG:
            print('做多（加仓）')
            trade.direction = constant.DIRECTION_LONG
            trade.offset = constant.OFFSET_OPEN
        elif data[4] > 0 and self.direction == constant.DIRECTION_SHORT:
            print('买平')
            trade.direction = constant.DIRECTION_LONG
            trade.offset = constant.OFFSET_CLOSE
        elif data[4] > 0 and self.direction == constant.DIRECTION_NET:
            print('买开')
            trade.direction = constant.DIRECTION_LONG
            trade.offset = constant.OFFSET_OPEN


        #如果成交数量 < 0 ，则分为3种情况，当仓位为空的时候，为"卖空"；当仓位为多的时候，为"卖平"；当仓位为空时候，为"卖空加仓"；默认都是一次性全部成交
        elif data[4] < 0 and self.direction == constant.DIRECTION_LONG:
            print('卖平')
            trade.direction = constant.DIRECTION_SHORT
            trade.offset = constant.OFFSET_CLOSE
        elif data[4] < 0 and self.direction == constant.DIRECTION_SHORT:
            print('做空加仓')
            trade.direction = constant.DIRECTION_SHORT
            trade.offset = constant.OFFSET_OPEN
        elif data[4] < 0 and self.direction == constant.DIRECTION_NET:
            print('卖开')
            trade.direction = constant.DIRECTION_SHORT
            trade.offset = constant.OFFSET_OPEN

        trade.price = data[5]                                        # 成交的价格
        buf, trade.tradeTime = self.generateDateTime(data[2])        # 成交的时间

        """
        这里做了特殊的处理为了与引擎关于维护各交易币种的仓位；在原始引擎之中使用的是volume ,这里重新定义signalvolume 为交易数量为 正数
        """
        trade.volume = data[4]                       #这里volume 进行区别正负，显示在界面山
        trade.signalvolume = abs(data[4])
        self.gateway.onTrade(trade)

    # ----------------------------------------------------------------------
    def onSymbolDetails(self, data):
        """
        d {'pair': 'ltcusd', 
        'price_precision': 5,             #Maximum number of significant digits for price in this pair
        'initial_margin': '30.0',
        'minimum_margin': '15.0',
        'maximum_order_size': '5000.0',   # Maximum order size of the pair
        'minimum_order_size': '0.06',     # Minimum order size of the pair
        'expiration': 'NA', 
        'margin': True}                   # margin trading enabled for this pair

        #参考
            contract.priceTick = float(d['tick_size'])               # 下单价格精度   "0.01"
            contract.size = int(d['trade_increment'])                # 下单数量精度    "1",

        """
        for d in data:
            contract = VtContractData()
            contract.gatewayName = self.gatewayName
            contract.symbol = d['pair'].upper()                                 # btcusd ---->BTCUSD
            contract.exchange = constant.EXCHANGE_BITFINEX
            contract.vtSymbol = ':'.join([contract.symbol, contract.exchange])  # 合约在vt系统中的唯一代码，通常是 合约代码:交易所代码
            contract.name = contract.vtSymbol                                   # 合约中文名
            contract.productClass = constant.PRODUCT_SPOT                                # 现货交易

            # contract.size = 1                                                          # 合约大小 数字货币现货合约大小为1
            # contract.priceTick = pow(10, d["price_precision"])                         # 10 的5次方
            # contract.price_precision = d["price_precision"]

            contract.size = float(d['minimum_order_size'])                               # 下单数量精度
            contract.price_precision = d["price_precision"]                              # 价格精度
            contract_priceTick = pow(10, -int(d["price_precision"]))                     # 下单价格精度
            contract.priceTick = self.as_num(contract_priceTick)
            self.gateway.onContract(contract)

        self.writeLog(u'合约各币种信息查询成功')

    def as_num(self, x):
        y = '{:.5f}'.format(x)                                                          # 5f表示保留5位小数点的float型
        return y

    # ----------------------------------------------------------------------
    def onQueryPosition(self, data):
        """
        查询持仓，查询各个币种的仓位
        """
        """
        [{'id': 140620317, 'symbol': 'eosusd', 'status': 'ACTIVE', 'base': '5.0615', 'amount': '6.0', 'timestamp': '1557243822.0', 'swap': '0.0', 'pl': '0.0817764'},
        {'id': 140620418, 'symbol': 'xrpusd', 'status': 'ACTIVE', 'base': '0.3149', 'amount': '16.0', 'timestamp': '1557245090.0', 'swap': '0.0', 'pl': '-0.01199296'}
        ]
          "id":943715,
          "symbol":"btcusd",
          "status":"ACTIVE",
          "base":"246.94",
          "amount":"1.0",
          "timestamp":"1444141857.0",
          "swap":"0.0",
          "pl":"-2.22042"

        """
        for d in data:
            if float(d['amount']) > 0:                                                                   # 注意字符串转换成浮点数字
                longPosition = VtPositionData()
                longPosition.gatewayName = self.gatewayName
                longPosition.symbol = d['symbol'].upper()                                                # btcusd ---->BTCUSD
                longPosition.exchange = constant.EXCHANGE_BITFINEX
                longPosition.vtSymbol = ':'.join(
                    [longPosition.symbol, longPosition.exchange])                                       # 合约在vt系统中的唯一代码，通常是 合约代码:交易所代码
                longPosition.direction = constant.DIRECTION_LONG                                                 # 定义到头仓位
                longPosition.vtPositionName = ':'.join([longPosition.vtSymbol, longPosition.direction])
                longPosition.price = float(d['base'])
                longPosition.positionProfit = float(d['pl'])
                longPosition.position = abs(float(d['amount']))
                self.gateway.onPosition(longPosition)
            elif float(d['amount']) < 0:
                shortPosition = VtPositionData()
                shortPosition.gatewayName = self.gatewayName
                shortPosition.symbol = d['symbol'].upper()                                               # btcusd ---->BTCUSD
                shortPosition.exchange = constant.EXCHANGE_BITFINEX
                shortPosition.vtSymbol = ':'.join(
                    [shortPosition.symbol, shortPosition.exchange])                                      # 合约在vt系统中的唯一代码，通常是 合约代码:交易所代码
                shortPosition.position = abs(float(d['amount']))
                shortPosition.direction = constant.DIRECTION_SHORT
                shortPosition.vtPositionName = ':'.join([shortPosition.vtSymbol, shortPosition.direction])
                shortPosition.price = float(d['base'])
                shortPosition.positionProfit = float(d['pl'])
                self.gateway.onPosition(shortPosition)

        self.writeLog(u'各个币种仓位初始化查询成功')


    # ----------------------------------------------------------------------
    def onQueryAccount(self, data):
        """
        这里是onqueryaccount 的回调函数，使用restful api 去bitfinex 交易接口之中的资金量usd 包含usd 资金的杠杆等信息
        :param data:
        :return:
        """
        pd = data[0]
        account = VtAccountData()
        account.vtAccountID = self.gatewayName
        account.gatewayName = self.gatewayName
        account.accountID = 'USD'                                                           # 交易的币种
        account.vtAccountID = ':'.join([account.gatewayName, account.accountID])

        account.balance = float(pd['margin_balance'])                                       # 账户净值
        account.available = float(pd['net_value'])                                          # 可用资金
        account.margin = float(pd['required_margin'])                                       # 保证金占用
        account.positionProfit = float(pd['unrealized_pl'])                                 # 持仓盈亏
        # account.closeProfit = float(d['realized_pnl'])
        self.gateway.onAccount(account)

        """
            [
                  {
                  'margin_balance':'36.82040937',
                  'tradable_balance':'92.051023425',
                  'unrealized_pl':'0.0',
                  'unrealized_swap':'0.0',
                  'net_value':'36.82040937',
                  'required_margin':'0.0',
                  'leverage':'2.5',
                  'margin_requirement':'0.0',
                  'margin_limits':[
                                        {
                                        'on_pair':'BTCUSD',
                                        'initial_margin':'30.0',
                                        'margin_requirement':'15.0',
                                        'tradable_balance':'122.665575089'
                                        },
                  'message':'Margin requirement,leverage and tradable balance are now per pair. 
                  Values displayed in the root of the JSON message are incorrect (deprecated). 
                  You will find the correct ones under margin_limits,for each pair. Please update your code as soon as possible.'
                  }
            ]
        """

    # ----------------------------------------------------------------------------发送钉钉消息，id填上使用的机器人的id
    def send_dingding_msg(self, content, robot_id=''):
        try:
            msg = {
                "msgtype": "text",
                "text": {"content": content + '\n' + datetime.now().strftime("%m-%d %H:%M:%S")}}
            headers = {"Content-Type": "application/json;charset=utf-8"}
            url = 'https://oapi.dingtalk.com/robot/send?access_token=' + robot_id
            body = json.dumps(msg)
            requests.post(url, data=body, headers=headers)
            print('成功发送钉钉')
        except Exception as e:
            print("发送钉钉失败:", e)

    #---------------------------------------------------------------------------------- 获取bitfinex交易所k线
    def get_bitfinex_candle_data(self, exchange, symbol, time_interval, limit):
        while True:
            try:
                content = exchange.fetch_ohlcv(symbol=symbol, timeframe=time_interval, limit=limit)
                break
            except Exception as e:
                self.send_dingding_msg(content='抓不到k线，稍等重试')
                print(e)
                sleep(5 * 1)

        df = pd.DataFrame(content, dtype=float)
        df.rename(columns={0: 'MTS', 1: 'open', 2: 'high', 3: 'low', 4: 'close', 5: 'volume'}, inplace=True)
        df['candle_begin_time'] = pd.to_datetime(df['MTS'], unit='ms')
        df['candle_begin_time_GMT8'] = df['candle_begin_time'] + timedelta(hours=8)
        # df = df[['candle_begin_time_GMT8', 'open', 'high', 'low', 'close', 'volume']]
        # 在这里使用的是中国本地时间 所以需要GMT8 如果在服务器上跑直接使用candle_begin_time这一列就可以了
        df = df[['candle_begin_time', 'open', 'high', 'low', 'close', 'volume']]
        return df





