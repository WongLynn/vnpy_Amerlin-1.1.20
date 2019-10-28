# encoding: UTF-8
"""
author: lynnwong
date:2019-08-06
v1 本策略是基于海龟的单标的的交易策略

单标的海龟交易策略，实现了完整海龟策略中的信号部分。本策略适合在vnpy_fxday 之中进行使用
海龟交易策略额基本的思路：
n1 根k线的最高价的 最大值，当收盘价高于这个价格时候开多
n1 根k线的最低价的 最低值，当收盘价低于这个价格时候开空

n2 根k线的最高价的 最大值，当收盘价高于这个价格时候平空
n2 根k线的最低价的 最小值，当收盘价低于这个价格时候平多
所以我们将n1 作为入场通道，计算入场通道的最高价，最大值；最低价最小值
同理我们计算n2作为出场的通道

我们通过atr 的波动率作为逐步进行加仓的依据
v1: 适合进行实盘的信号的验证
v2: 根据实盘的经验这里需要增加滑点  这里的滑点按照百分比进行增加,海龟是没有滑点的
v3: 该策略是5分钟策略主要用于信号的验证

"""


from __future__ import division

from vnpy.trader.language.chinese.constant import DIRECTION_LONG, DIRECTION_SHORT,STATUS_UNKNOWN

from vnpy.trader.app.ctaStrategy.ctaTemplate import (CtaTemplate)
from time import sleep
from datetime import datetime, timedelta
from vnpy.trader.vtConstant import *
from vnpy.trader.vtObject import VtBarData

import json
import requests
import ccxt
import os
import time
import numpy as np

from tabulate import tabulate
tabulate.PRESERVE_WHITESPACE = True

import pandas as pd
from pandas import Series, DataFrame
pd.set_option('expand_frame_repr', False)  # 当列太多时不换行

# ================================================================================参数
# '1m', '5m', '15m', '30m', '1h', '3h', '6h', '12h', '1D', '7D', '14D', '1M'
time_interval = '5m'      # 间隔运行时间，不能低于5min,注意这里的时间间隔是在ccxt 之中的参数

bitfinex = ccxt.bitfinex2()
bitfinex.apiKey = 'vJpJpkE8kWk'
bitfinex.secret = 'JN4yn90m1zVNmXA'


###############################################################################################
class TurtleTradingSpradeStrategy(CtaTemplate):
    className = 'TurtleTradingSpradeStrategy'
    author = 'yalinwang'
    version = '1.1.20'

    # 策略交易标的的列表,这个是对监听程序之中进行trade/order/position监听使用
    symbolList = []                     # 初始化为空
    tradeList = []                      # 对交易之后的orderid 进行判断


    # 策略参数 指在策略之中的指标参数，在回测的时候可以进行修改
    entryWindow = 55                    # 入场通道窗口
    exitWindow = 70                    # 出场通道窗口
    atrWindow = 40                      # 计算ATR波动率的窗口
    fixedSize = 20                       # 每次交易的数量
    losspercent = 0.005                 # 这里使用滑点，百分比滑点 千分比滑点


    # 策略变量
    entryUp = 0                         # 入场通道上轨
    entryDown = 0                       # 入场通道下轨
    exitUp = 0                          # 出场通道上轨
    exitDown = 0                        # 出场通道下轨
    atrVolatility = 0                   # ATR波动率

    longEntry = 0                       # 多头入场价格
    shortEntry = 0                      # 空头入场价格
    longStop = 0                        # 多头止损价格
    shortStop = 0                       # 空头止损价格


    """
    根据对海龟策略的理解我们可以这么认为：
    当bar.close > enterup 的时候     ”买开“
    当bar .close< entryDown 的时候   ”卖开“
    当bar.close > exitUp 的时候     ”买平“（平空仓）
    当bar .close< exitDown 的时候   ”卖平“（平多仓）

    这里是cta 策略之中的经典的策略，海龟策略是在前一个交易日将信号计算出来，在下一个交易日时候，
    突破即买入，这里的出场的条件是进行atr 波动止损出场，平仓条件是海龟自己的条件，所以使用停止单

    """

    #-------------------------------------------------------------------------------------------------
    # 参数列表，保存了参数的名称，这里保留的参数名称将会显示在控制台
    # 这三项必须要有 classname   策略名字 、author      策略作者 、symbollist  策略交易标的列表
    #-------------------------------------------------------------------------------------------------
    paramList = ['className',
                 'author',
                 'symbolList',
                'entryWindow',
                 'exitWindow',
                 'atrWindow',
                 'fixedSize']
    #-------------------------------------------------------------------------------------------------
    # 变量列表，保存了变量的名称,这里的变量的名字是策略执行之中的变量可以变动，在控制中台
    # 这三项必须要有 inited',     是否启动 、'trading',   是否实盘 、'posDict',   现有仓位
    #-------------------------------------------------------------------------------------------------
    varList = ['inited',
               'trading',
               'posDict',
               'entryUp',
               'entryDown',
               'exitUp',
               'exitDown',
               'atrVolatility',
               'longEntry',
               'shortEntry',
               'longStop',
               'shortStop']

    # 同步列表，保存了需要保存到数据库的变量名称，下面同步的项目均为在ctaenging之中
    syncList = ['posDict', 'eveningDict', 'accountDict']

    #----------------------------------------------------------------------------------------------
    def __init__(self, ctaEngine, setting):
        super(TurtleTradingSpradeStrategy, self).__init__(ctaEngine, setting)


    #---------------------------------------------------------------------------------------------
    def onInit(self):
        """初始化策略（必须由用户继承实现）"""

        # 生成所有品种相应的 bgDict 和 amDict，用于存放一定时间长度的行情数据，时间长度size默认值是100
        # 引擎支持的常用分钟数为：1, 5, 10, 15, 20, 30, 60, 120, 240, 360, 480
        # 示例说明： self.generateBarDict( self.onBar, 5, self.on5MinBar)
        #       将同时生成 self.bg5Dict 和 self.am5Dict ,字典的key是品种名,
        #       用于生成 on5MinBar 需要的 Bar 和计算指标信号用的 bar array，可在 on5MinBar() 获取到
        self.generateBarDict(self.onBar)
        self.generateBarDict(self.onBar,5,self.on5MinBar,size =100)
        """
        注意这里的size 是要进行判断是的，所以建议参考使用最大的参数的 3倍数字
        
        """

        # 回测和实盘的获取历史数据部分，建议实盘初始化之后得到的历史数据和回测预加载数据交叉验证，确认代码正确
        engine = self.getEngineType()
        if engine == 'backtesting':
            # 获取回测设置中的initHours长度的历史数据，并直接按照回测模式推送到 onBar 或者 onTick
            self.initBacktesingData()

        elif engine == 'trading':
            # 实盘从交易所载入1分钟实时历史数据，并采用回放计算的方式初始化策略参数
            # 通用可选参数：["1min","5min","15min","30min","60min","120min","240min","1day","1week","1month"]
            # CTP 只提供 1min 数据，因为数据源没有限制长度，所以不接受数量请求，请使用since = '20180901'这样的参数请求
            kline1,kline60,kline15 ,kline5={},{},{},{}
            #  注意这里的周期的顺序是从大周期开始的  这里数据直接调用ctatemple 然后是
            for s in self.symbolList:
                # 针对每个交易对分别进行历史数据的加载  注意这里是从大周期开始的
                kline5[s] = self.loadHistoryBar(s, '5min',1000)[:-100]            #这里是调用先关的周期的最大值  注意这里返回的其实是df 数据格式
                print("kline5[s]",kline5[s])
                kline1[s] = self.loadHistoryBar(s, '1min',1200)
            # 更新数据矩阵 (optional)
            for s in self.symbolList:
                for bar in kline5[s]:
                    self.am5Dict[s].updateBar(bar)
                for bar in kline1[s]:
                    self.onBar(bar)

        self.putEvent()  # putEvent 能刷新策略UI界面的信息
        '''
        实盘在初始化策略时, 如果将历史数据推送到onbar去执行updatebar, 此时引擎的下单逻辑为False, 不会触发下单。
        '''
    #----------------------------------------------------------------------
    def onStart(self):
        """启动策略（必须由用户继承实现）"""
        self.writeCtaLog(u'%s策略启动' %self.name)
        self.mail('程序重新启动')
        self.putEvent()
        '''
        实盘在点击启动策略时, 此时的引擎下单逻辑改为True, 此时开始推送到onbar的数据, 会触发下单。
        '''
    #----------------------------------------------------------------------
    def onStop(self):
        """停止策略（必须由用户继承实现）"""
        self.writeCtaLog(u'%s策略停止' %self.name)
        self.putEvent()

    #----------------------------------------------------------------------
    def onRestore(self):
        """恢复策略（必须由用户继承实现）"""
        # 策略恢复会自动读取 varList 和 syncList 的数据，还原之前运行时的状态。
        # 需要注意的是，使用恢复，策略不会运行 onInit 和 onStart 的代码，直接进入行情接收阶段
        self.putEvent()

    #----------------------------------------------------------------------
    def onTick(self, tick):
        """收到行情TICK推送（必须由用户继承实现）"""
        # 在每个Tick推送过来的时候,进行updateTick,生成分钟线后推送到onBar.
        # 需要注意的是，如果没有updateTick，实盘将不会推送1分钟K线
        self.bgDict[tick.vtSymbol].updateTick(tick)


    #----------------------------------------------------------------------
    def onBar(self, bar):
        """收到Bar推送（必须由用户继承实现）
        收到tickr 推送之后每一份中合成之中，推送到bar 函数之中，进行bar 播报
        同时将1分钟bar 合并到成X分钟的bar
        """
        symbol = bar.vtSymbol
        self.writeCtaLog(u'%s, bar.OPEN%s, bar.HIGH%s, bar.LOW%s, bar.CLOSE%s, %s' % (symbol, bar.open, bar.high, bar.low, bar.close, bar.datetime))
        # 需要将 Bar 数据同时推给 5MinBar  bg字典 去合成
        self.bg5Dict[symbol].updateBar(bar)

        self.putEvent()

    #----------------------------------------------------------------------
    def on5MinBar(self, bar):
        """
        收到Bar推送（必须由用户继承实现）
        这里指当bargenerater 合成之后，将bar推送到该函数之中；
        根据测试，ctaenging 维护的stoporder[] order[] 在进行新的指标计算时候可能存在没有执行的订单order,所以在新的周期开始之前需要去掉所有的订单信息
            根据对海龟策略的理解我们可以这么认为：
            当bar.close > enterup 的时候     ”买开“
            当bar .close< entryDown 的时候   ”卖开“
            当bar.close > exitUp 的时候      ”买平“（平空仓）
            当bar .close< exitDown 的时候    ”卖平“（平多仓）
        海龟交易策略是提前计算好指标，在价格突破指标的状态下，进行突破买入停止单买入，注意在策略之中买入 与 卖出 的数量均为 正数
        """
        self.cancelAllStopOrder()
        self.writeCtaLog('取消所有本地停止单')

        self.cancelAll()
        self.writeCtaLog('取消单')

        """
        vnpy 原始计算指标生成方法，是使用引擎之中的arraymanager  根据numpy 进行技术指标的计算
        
        """
        symbol = bar.vtSymbol
        self.am5Dict[symbol].updateBar(bar)       # 需要将 5MinBar 数据同时推给 5MinBar 的array字典去保存，用于talib计算
        am5 = self.am5Dict[symbol]                # am5 是array 用来计算的数组
        # self.writeCtaLog(u' am60 %s' % am5.__dict__)
        if not am5.inited:                        # 用于判断数组的数量是不是符合这里是实盘的历史数据最好可以满足》size
            return

        self.entryUp, self.entryDown = am5.donchian(self.entryWindow)
        self.exitUp, self.exitDown = am5.donchian(self.exitWindow)

        self.writeCtaLog('%son5minBar, entryUp%s, entryDown%s, exitUp%s, exitDown%s' % (symbol,self.entryUp, self.entryDown, self.exitUp,self.exitDown))
        # 在仓位为0 的时候进行计算出来atrVolatility 用于计算逐仓买入的价格； 以及止损出场的价格
        if self.posDict[symbol+"_LONG"] == 0 and self.posDict[symbol+"_SHORT"] == 0 :
            self.atrVolatility = am5.atr(self.atrWindow)
            self.writeCtaLog("仓位为空，计算 self.atrVolatility %s "%(self.atrVolatility))

        if self.posDict[symbol+"_LONG"] == 0 and self.posDict[symbol+"_SHORT"] == 0:
            self.longEntry = 0                                                           #多头入场价格
            self.shortEntry = 0                                                          #空头入场价格
            self.longStop = 0                                                            #多头止损价格
            self.shortStop = 0                                                           #空头止损价格
            self.sendBuyOrders(symbol,self.entryUp)                                      #入场通道上轨进行买入入场
            self.sendShortOrders(symbol,self.entryDown)                                  #入场通道下轨进行做空入场

        elif self.posDict[symbol+"_LONG"] > 0:
            self.sendBuyOrders(symbol,self.longEntry)
            sellPrice = max(self.longStop, self.exitDown)
            self.writeCtaLog("sellPrice 策略执行 多头止损 %s " %(sellPrice))

            self.sell(symbol,sellPrice, abs(self.posDict[symbol+"_LONG"]), True)
            self.writeCtaLog("策略执行多头止损或平仓  sellPrice ")

        elif self.posDict[symbol+"_SHORT"] > 0:
            self.sendShortOrders(symbol,self.shortEntry)
            coverPrice = min(self.shortStop, self.exitUp)
            self.writeCtaLog("策略执行空头止损 coverPrice %s"%(coverPrice))

            self.cover(symbol,coverPrice, abs(self.posDict[symbol+"_SHORT"]), True)
            self.writeCtaLog("执行空头平仓或止损  coverPrice ")

        # 发出状态更新事件
        self.putEvent()


    #----------------------------------------------------------------------
    def onOrder(self, order):
        """
        收到委托变化推送（必须由用户继承实现），对于无需做细粒度委托控制的策略，可以忽略onOrder，在这里我们进行了详细的
        控制，根据发出订单之后的委托的信息，进行邮件通知，通知自己的订单order的方向，根据ctaenging  processonorderevent
        的含义，将策略之中的成交的信息进行播报
        针对bitfinex gateway 改造，以及对ctaenging的改造，这里使用了 signalTradedVolume 进行播报
        """
        if order.status == STATUS_UNKNOWN:
            self.mail(u'出现未知订单，需要策略师外部干预,ID:%s, symbol:%s,direction:%s,offset:%s'
                 %(order.vtOrderID, order.vtSymbol, order.direction, order.offset))
        if order.tradedVolume != 0 :
            content = u'成交信息播报,ID:%s, symbol:%s, directionL%s, offset:%s, price:%s, amount:%s'%(order.vtOrderID, order.vtSymbol,
                                                                order.direction, order.offset, order.price,order.tradedVolume)
            self.mail(content)


    #----------------------------------------------------------------------
    def onTrade(self, trade):
        """
        成交推送，这里对ontrade 进行了细力粒度的控制，用于控制多投止损与空头止损的价位，即在首次进行开仓时候，不管是做空还是做多就
        决定了止损的价格的存在，前提是交易成功
        同时将止损价格设定为2个atr 波动率
        """
        if trade.direction == DIRECTION_LONG:
            self.longEntry = trade.price
            self.longStop = self.longEntry - self.atrVolatility * 2
        else:
            self.shortEntry = trade.price
            self.shortStop = self.shortEntry + self.atrVolatility * 2

        self.putEvent()


    #---------------------------------------------------------------------
    def onStopOrder(self, so):
        """停止单推送"""
        pass

    #----------------------------------------------------------------------
    def sendBuyOrders(self, symbol,price):
        """
        这里定义了海龟交易的逐仓的方式，每次固定交易量fixedsize 以及根据变动atrvoltility ；
        这里对发单函数进行类，规定了两个方向分别是发多单以及发空单
        主要区别原版的vnpy 这里添加了一个参数symbol,同时这里使用突破买入止损单操作
        第一次买入的时候，t=0 所以 直接买入  买入数量为一个单位 1
        第二次买入的时候，pos=1 此时的t =1 此时的价格是上一个加以价格加上滑价
        """
        t = self.posDict[symbol+"_LONG"] / self.fixedSize

        if t < 1:
            self.buy(symbol,price, self.fixedSize, True)

        if t < 2:
            self.buy(symbol,price + self.atrVolatility*0.5, self.fixedSize,True)

        if t < 3:
            self.buy(symbol,price + self.atrVolatility, self.fixedSize, True)

        if t < 4:
            self.buy(symbol,price + self.atrVolatility*1.5, self.fixedSize, True)

    #----------------------------------------------------------------------
    def sendShortOrders(self, symbol,price):
        """
        在vnpyfxday 之宗的pos 全部为 正数，所以这里的计算分仓位买入都是 正数
        这里是使用固定数量的交易的方式进行交易
        分批次开仓的逻辑是使用波动率，这里 atrVolatility 是在仓位为0时计算出来的
        :param symbol:
        :param price:
        :return:
        """
        t = self.posDict[symbol+"_SHORT"]/ self.fixedSize

        if t < 1:
            self.short(symbol,price, self.fixedSize, True)


        if t < 2:
            self.short(symbol,price - self.atrVolatility*0.5, self.fixedSize, True)


        if t < 3:
            self.short(symbol,price - self.atrVolatility, self.fixedSize, True)


        if t < 4:
            self.short(symbol,price - self.atrVolatility*1.5, self.fixedSize, True)


    # =================================================================获取bitfinex交易所k线
    def get_bitfinex_candle_data(self,exchange, symbol, time_interval, limit):
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
        #df = df[['candle_begin_time_GMT8', 'open', 'high', 'low', 'close', 'volume']]
        # 在这里使用的是中国本地时间 所以需要GMT8 如果在服务器上跑直接使用candle_begin_time这一列就可以了
        df = df[['candle_begin_time', 'open', 'high', 'low', 'close', 'volume']]
        return df


    # =====发送钉钉消息，id填上使用的机器人的id
    def send_dingding_msg(self, content, robot_id='5cc1fc681a063467cec845'):
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


    # =======================================================将交易对转化成ccxt 的交易对信息
    def symbol_ccxt(self, symbol):
        tradesymbol = symbol.split(':')[0]        # EOSUSD
        tradeexchange = symbol.split(':')[-1]     # BITFINEX

        base_coin = tradesymbol[:-3]
        trade_coin = tradesymbol[-3:]
        if tradeexchange == 'BITFINEX':
            symbol_ccxt = base_coin + '/' + trade_coin
        elif tradeexchange == 'OKEX':
            symbol_ccxt = base_coin + '/' + trade_coin + 'T'

        print(symbol_ccxt)
        return symbol_ccxt

    # ================================通过ccxt获取margin的仓位     注意这个历史上也发生过错误信息
    def ccxt_fetch_margin_position(self,bitfinex2):
        # 获取账户的margin持仓信息
        while True:
            try:
                position_info = bitfinex2.private_post_auth_r_positions()  # 从bfx交易所获取账户的持仓信息
                break
            except Exception as e:
                self.send_dingding_msg('获取持仓信息失败')
                print(e)
                continue
        position_info = pd.DataFrame(position_info)
        position_info.rename(columns={0:'交易对', 1:'状态', 2:'持仓量', 3:'成本价格', 4:'借币利息',
                                                             5:'unknow1', 6:'损益', 7:'损益比例',8:'爆仓价格',9:'unknow2',
                                                             },inplace=True)      #将数据转换成df 格式
        if len(position_info) >0:
            position_info.drop(['状态', 'unknow1', 'unknow2'], axis=1, inplace=True)  # 去除不必要的列

        return position_info


    # ============================================================通过ccxt获取margin的usd余额
    def ccxt_fetch_margin_usd_amount(bitfinex2):
        # =====获取当前资金数量
        while True:
            try:
                margin_info = bitfinex2.private_post_auth_r_wallets()
                break
            except Exception as e:
                print(e)
        account = pd.DataFrame(margin_info, columns=['交易账户', '币种', '数量', 'unknow', 'unknow2'])  # 将数据转化为df格式
        condition1 = account['交易账户'] == 'margin'
        condition2 = account['币种'] == 'USD'
        usd_amount = float(account.loc[condition1 & condition2, '数量'])
        return usd_amount




#----------------------------------------------------------------------------------------------------------------------
#交易回测模块 直接在策略上进行回测

from vnpy.trader.app.ctaStrategy.ctaBacktesting import BacktestingEngine
import json


Dbname = 'VnTrader_1Min_Db'


if __name__ == '__main__':
    # from StrategyBollBand import BollBandsStrategy

    # 创建回测引擎
    engine = BacktestingEngine()

    # 设置引擎的回测模式为K线
    engine.setBacktestingMode(engine.BAR_MODE)

    # 设置使用的历史数据库
    engine.setDB_URI("mongodb://localhost:32768")
    engine.setDatabase('VnTrader_1Min_Db')

    # 设置回测用的数据起始日期，initHours 默认值为 0
    engine.setStartDate('20181214 23:00:00', initHours=120)
    engine.setEndDate('20190314 23:00:00')

    # 设置产品相关参数
    engine.setCapital(1000000)      # 设置起始资金，默认值是1,000,000
    # 设置交易合约对的回测的参数值
    contracts = [
        {"symbol":"ETHUSD:BITFINEX",
        "size" : 10,
        "priceTick" : 0.001,
        "rate" : 5/10000,
        "slippage" : 0.005
        }]
    engine.setContracts(contracts)             # 设置回测合约相关数据

    # =============配置策略报告的输出的路径，这里是macos 的路径的输出
    # 策略报告默认为False不输出，True为输出，且默认输出目录路径于当前文件夹下
    engine.setLog(True, path = "vnpy_data")

    # 设置本地数据缓存的路径，默认数据缓存存放目录: ~/vnpy_data
    engine.setCachePath("vnpy_data")


    # =============配置策略报告的输出的路径，这里是window 的路径的输出
    # 策略报告默认为False不输出，True为输出，且默认输出目录路径于当前文件夹下
    # engine.setLog(True, path = "D:\\vnpy_data\\")

    # 设置本地数据缓存的路径，默认数据缓存存放目录: ~/vnpy_data
    # engine.setCachePath("D:\\vnpy_data\\")


    # 在引擎中创建策略对象     注意对其中的修改
    with open("CTA_setting.json") as parameterDict:
        setting = json.load(parameterDict)[1]
        print("setting",setting)

    #初始化回测先关值
    engine.initStrategy(TurtleTradingSpradeStrategy, setting)

    # 开始跑回测
    engine.runBacktesting()

    # 显示回测结果
    engine.showBacktestingResult()
    engine.showDailyResult()


# """
# 展示如何执行参数优化。
# """
# from vnpy.trader.app.ctaStrategy.ctaBacktesting import BacktestingEngine, OptimizationSetting
#
# if __name__ == '__main__':
#
#
#     # 创建回测引擎
#     engine = BacktestingEngine()
#
#     # 设置引擎的回测模式为K线
#     engine.setBacktestingMode(engine.BAR_MODE)
#
#     # 设置使用的历史数据库
#     engine.setDB_URI("mongodb://localhost:32768")
#     engine.setDatabase("VnTrader_1Min_Db")
#
#     # 设置回测用的数据起始日期
#     engine.setStartDate('20181214 23:00:00', initHours=120)
#     engine.setEndDate('20190314 23:00:00')
#
#     # #设置产品相关参数
#     contracts = [
#         {"symbol": "ETHUSD:BITFINEX",
#          "size": 10,
#          "priceTick": 0.001,
#          "rate": 5 / 10000,
#          "slippage": 0.005
#          }]
#
#     engine.setContracts(contracts)  # 设置回测合约相关数据
#
#     # 跑优化
#     setting = OptimizationSetting()  # 新建一个优化任务设置对象
#     setting.setOptimizeTarget('totalNetPnl')  # 设置优化排序的目标是策略净盈利
#
#
#     setting.addParameter('entryWindow', 55, 60, 5)  # 增加第一个优化参数atrLength，起始12，结束20，步进2
#     setting.addParameter('exitWindow', 70, 75, 5)  # 增加第二个优化参数atrMa，起始20，结束30，步进5
#     setting.addParameter('atrWindow', 40)        # 增加一个固定数值的参数
#
#
#     # 性能测试环境：I7-3770，主频3.4G, 8核心，内存16G，Windows 7 专业版
#     # 测试时还跑着一堆其他的程序，性能仅供参考
#     import time
#     start = time.time()
#
#     # # 运行单进程优化函数，自动输出结果，耗时：359秒
#     # engine.runOptimization(BollBandsStrategy, setting)
#
#     # 多进程优化，耗时：89秒
#     engine.runParallelOptimization(TurtleTradingSpradeStrategy, setting)
#
#     print(u'耗时：%s' % (time.time() - start))
#





