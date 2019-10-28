# encoding: UTF-8

'''
本文件中实现了CTA策略引擎，针对CTA类型的策略，抽象简化了部分底层接口的功能。

关于平今和平昨规则：
1. 普通的平仓OFFSET_CLOSET等于平昨OFFSET_CLOSEYESTERDAY
2. 只有上期所的品种需要考虑平今和平昨的区别
3. 当上期所的期货有今仓时，调用Sell和Cover会使用OFFSET_CLOSETODAY，否则
   会使用OFFSET_CLOSE
4. 以上设计意味着如果Sell和Cover的数量超过今日持仓量时，会导致出错（即用户
   希望通过一个指令同时平今和平昨）
5. 采用以上设计的原因是考虑到vn.trader的用户主要是对TB、MC和金字塔类的平台
   感到功能不足的用户（即希望更高频的交易），交易策略不应该出现4中所述的情况
6. 对于想要实现4中所述情况的用户，需要实现一个策略信号引擎和交易委托引擎分开
   的定制化统结构（没错，得自己写）
   # 优化了关键日志用于复盘检验交易情况
   # 针对bitfinex 做了优化处理
'''


from __future__ import division
import json
import os
import traceback
import importlib
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta
from copy import copy
from vnpy.event import Event
from vnpy.trader.vtEvent import *
from vnpy.trader.vtConstant import *
from vnpy.trader.vtObject import VtTickData, VtBarData
from vnpy.trader.vtGateway import VtSubscribeReq, VtOrderReq, VtCancelOrderReq, VtLogData
from vnpy.trader.vtFunction import todayDate, getJsonPath
from vnpy.trader.utils.email import mail
from decimal import *

from .ctaBase import *
from .strategy import STRATEGY_CLASS


########################################################################
class CtaEngine(object):
    """CTA策略引擎"""
    settingFileName = 'CTA_setting.json'
    settingfilePath = getJsonPath(settingFileName, __file__)

    #----------------------------------------------------------------------
    def __init__(self, mainEngine, eventEngine):
        """Constructor"""
        #定义日志模块

        self.mainEngine = mainEngine
        self.eventEngine = eventEngine

        # 当前日期
        self.today = todayDate()
        self.minute_temp = 0

        # 保存策略实例的字典
        # key为策略名称，value为策略实例，注意策略名称不允许重复
        self.strategyDict = {}

        # 保存vtSymbol和策略实例映射的字典（用于推送tick数据）
        # 由于可能多个strategy交易同一个vtSymbol，因此key为vtSymbol
        # value为包含所有相关strategy对象的list
        self.tickStrategyDict = {}

        # 保存vtOrderID和strategy对象映射的字典（用于推送order和trade数据）
        # key为vtOrderID，value为strategy对象
        self.orderStrategyDict = {}

        # 本地停止单编号计数
        self.stopOrderCount = 0
        # stopOrderID = STOPORDERPREFIX + str(stopOrderCount)

        # 本地停止单字典
        # key为stopOrderID，value为stopOrder对象
        self.stopOrderDict = {}             # 停止单撤销后不会从本字典中删除
        self.workingStopOrderDict = {}      # 停止单撤销后会从本字典中删除

        # 保存策略名称和委托号列表的字典
        # key为name，value为保存orderID（限价+本地停止）的集合
        self.strategyOrderDict = {}
        # 成交号集合，用来过滤已经收到过的成交推送
        self.tradeSet = set()

        # 引擎类型为实盘
        self.engineType = ENGINETYPE_TRADING

        # 注册日式事件类型
        self.mainEngine.registerLogEvent(EVENT_CTA_LOG)

        # 注册事件监听
        self.registerEvent()

        self.path = os.path.join(os.getcwd(), u"reports" )
        if not os.path.isdir(self.path):
            os.makedirs(self.path)

        # 上期所昨持仓缓存
        self.ydPositionDict = {}
    #-------------------------------------------------------------------------------------------------------------------------
    def sendOrder(self, vtSymbol, orderType, price, volume, priceType, strategy):
        """
        发单
        strategy 产生信号-----》ctatemple-----》注意这里传入的是“买开” “买平” “卖开” “卖平”
        """
        contract = self.mainEngine.getContract(vtSymbol)
        req = VtOrderReq()                                                                  #定义买单结构体
        reqList = []                                                                        #维系的订单列表
        reqcount = 1                                                                        #定义买单种类 1 代表非上期货

        req.symbol = contract.symbol
        req.exchange = contract.exchange
        req.vtSymbol = contract.vtSymbol
        # 这里最之前的价格修正的bug 进行了修改
        req.price = self.roundToPriceTick(contract.priceTick, price)
        req.volume = volume                                                                  #这里传入的都是由策略传入volume(+)

        req.productClass = strategy.productClass
        req.currency = strategy.currency
        req.byStrategy = strategy.name

        req.priceType = PRICETYPE_LIMITPRICE                                          # 设计为CTA引擎发出的委托只允许使用限价单

        # #这里修改可以发送限价单和市价单
        # req.priceType = priceType

        # CTA委托类型映射  cta策略底层委托映射 可以根据传入的ordertype求出来相应的direction 和 offset
        # 注意这里使用的bitfinex 由于bitfinex gateway api 没有开平，所以需要在gateway 之中进行定义转换
        if orderType == CTAORDER_BUY:                                                                #'买开'
            req.direction = DIRECTION_LONG
            req.offset = OFFSET_OPEN

        elif orderType == CTAORDER_SELL:                                                             #'买平'
            req.direction = DIRECTION_SHORT
            # 在"买平"状态下分为两种情况一种是上期所，分为今平与昨平
            if contract.exchange != EXCHANGE_SHFE:
                req.offset = OFFSET_CLOSE                                                           #非上期所
            #以下是上期所的平仓的操作  只有上期所才要考虑平今平昨
            else:
                # 获取持仓缓存数据
                posBuffer = self.ydPositionDict.get(vtSymbol+'_LONG', None)
                # 如果获取持仓缓存失败，则默认平昨
                if not posBuffer:
                    self.writeCtaLog(u'获取昨持多仓为0，发出平今指令')
                    req.offset = OFFSET_CLOSETODAY

                elif posBuffer:
                    if volume <= posBuffer:
                        req.offset = OFFSET_CLOSE
                        self.writeCtaLog(u'{}优先平昨，昨多仓:{}，平仓数:{}'.format(vtSymbol, posBuffer, volume))
                        req.offset = OFFSET_CLOSE
                        if (posBuffer - volume)>0:
                            self.writeCtaLog(u'{}剩余昨多仓{}'.format(vtSymbol,(posBuffer - volume)))
                    else:
                        req.offset = OFFSET_CLOSE
                        req.volume = posBuffer
                        #self.writeCtaLog(u'{}平仓量{}，大于昨多仓，拆分优先平昨仓数:{}'.format(vtSymbol, volume, posBuffer))
                        req2 = copy(req)
                        req2.offset = OFFSET_CLOSETODAY
                        req2.volume = volume - posBuffer
                        #self.writeCtaLog(u'{}平仓量大于昨多仓，拆分到平今仓数:{}'.format(vtSymbol, req2.volume))
                        reqcount = 2

        elif orderType == CTAORDER_SHORT:                                                           #'卖开'
            req.direction = DIRECTION_SHORT
            req.offset = OFFSET_OPEN

        elif orderType == CTAORDER_COVER:                                                           #'卖平'
            req.direction = DIRECTION_LONG
            if contract.exchange != EXCHANGE_SHFE:
                req.offset = OFFSET_CLOSE
            # 以下是上期所的平仓的操作  只有上期所才要考虑平今平昨
            else:
                # 获取持仓缓存数据
                posBuffer = self.ydPositionDict.get(vtSymbol+'_SHORT', None)
                # 如果获取持仓缓存失败，则默认平昨
                if not posBuffer:
                    self.writeCtaLog(u'获取昨持空仓为0，发出平今指令')
                    req.offset = OFFSET_CLOSETODAY

                elif posBuffer:
                    if volume <= posBuffer:
                        req.offset = OFFSET_CLOSE
                        self.writeCtaLog(u'{}优先平昨，昨空仓:{}，平仓数:{}'.format(vtSymbol, posBuffer, volume))
                        req.offset = OFFSET_CLOSE
                        if (posBuffer - volume)>0:
                            self.writeCtaLog(u'{}剩余昨空仓{}'.format(vtSymbol,(posBuffer - volume)))
                    else:
                        req.offset = OFFSET_CLOSE
                        req.volume = posBuffer
                        self.writeCtaLog(u'{}平仓量{}，大于昨空仓，拆分优先平昨仓数:{}'.format(vtSymbol, volume, posBuffer))
                        req2 = copy(req)
                        req2.offset = OFFSET_CLOSETODAY
                        req2.volume = volume - posBuffer
                        self.writeCtaLog(u'{}平仓量大于昨空仓，拆分到平今仓数:{}'.format(vtSymbol, req2.volume))
                        reqcount = 2

        #至此，我们完全求出来了发单的结构体
        # self.writeCtaLog("ctaenging sendorder  req structure%s"%(req.__dict__))

        # 委托转换 默认版本不转换了
        # reqList = self.mainEngine.convertOrderReq(req) # 不转了

        if reqcount == 1:
            reqList = [req]
        else:
            reqList = [req,req2]

        # 这里我需要进行确认 reqList  数组的结构以及顺序 是不是就是一个值，这里应该是的
        # self.writeCtaLog('ctaenging sendorder  reqList%s'%(reqList))

        vtOrderIDList = []                                                                           # 维系一个列表  vtOrderIDList
        # if not reqList:
        #     return vtOrderIDList
        for convertedReq in reqList:                                                                #从请求列表之中循环调取

            vtOrderID = self.mainEngine.sendOrder(convertedReq, contract.gatewayName)               #启用主引擎函数调动底层gateway,进行交易
            #这里返回 vtOrderID 的意义是什么 仅仅是返回而已，不能确保是否已经成交
            #self.writeCtaLog('-----ctaenging sendorder 交易接口发单 vtOrderID%s------'%(vtOrderID))

            # 保存vtOrderID和strategy对象映射的字典（用于推送order和trade数据）   key为vtOrderID，value为strategy对象
            # 针对每个订单 该订单对应的策略是......
            self.orderStrategyDict[vtOrderID] = strategy
            # self.writeCtaLog('---ctaenging sendorder  self.orderStrategyDict%s---'%(self.orderStrategyDict))



            # # 保存策略名称和委托号列表的字典  # key为name，value为保存orderID（限价+本地停止）的集合
            # 针对每个策略 对应的订单有.......

            self.strategyOrderDict[strategy.name].add(vtOrderID)
            # self.writeCtaLog('-----ctaenging sendorder  self.strategyOrderDict%s------'%(self.strategyOrderDict))


            # ----------------------------------------------------------------------------------------------------------
            # # 计算vnpy （限价+本地停止）list的集合
            # 这里将订单编号追加到订单编号列表之中
            # ----------------------------------------------------------------------------------------------------------
            vtOrderIDList.append(vtOrderID)

            # self.writeCtaLog('--ctaenging sendorder  vtOrderIDList%s--'%(vtOrderIDList))

            self.writeCtaLog('策略%s: 发送%s委托%s, 交易：%s，%s，数量：%s @ %s'
                         %(strategy.name, priceType, vtOrderID, vtSymbol, orderType, volume, price ))
        return vtOrderIDList



    #----------------------------------------------------------------------
    def cancelOrder(self, vtOrderID):                   #需要检查撤单是不是成功的，这一点需要进行检查  传入 BITFINEX:1554831039
        """撤单"""
        # 查询报单对象
        order = self.mainEngine.getOrder(vtOrderID)    #使用主引擎查看是否包含这个订单的信息，返回订单结构体
        #通过引擎查询委托情况

        # 如果查询成功
        if order:
            # 检查是否报单还有效，只有有效时才发出撤单指令
            orderFinished = (order.status==STATUS_ALLTRADED
                            or order.status==STATUS_CANCELLED
                            or order.status == STATUS_REJECTED
                            or order.status == STATUS_CANCELLING
                            or order.status == STATUS_CANCELINPROGRESS)

            if not orderFinished:
                req = VtCancelOrderReq()
                req.vtSymbol = order.vtSymbol
                req.symbol = order.symbol
                req.exchange = order.exchange
                req.frontID = order.frontID
                req.sessionID = order.sessionID
                req.orderID = order.orderID

                self.mainEngine.cancelOrder(req, order.gatewayName)
                self.writeCtaLog('策略%s: 对本地订单%s，品种%s发送撤单委托'%(order.byStrategy, vtOrderID, order.vtSymbol))

    def batchCancelOrder(self,vtOrderIDList):
        """批量撤单"""
        # 查询报单对象

        reqList = []
        for vtOrderID in vtOrderIDList:
            order = self.mainEngine.getOrder(vtOrderID)

            # 如果查询成功
            if order:
                # 检查是否报单还有效，只有有效时才发出撤单指令
                orderFinished = (order.status==STATUS_ALLTRADED
                                or order.status==STATUS_CANCELLED
                                or order.status == STATUS_REJECTED
                                or order.status == STATUS_CANCELLING
                                or order.status == STATUS_CANCELINPROGRESS)

                if not orderFinished:
                    req = VtCancelOrderReq()
                    req.vtSymbol = order.vtSymbol
                    req.symbol = order.symbol
                    req.exchange = order.exchange
                    req.frontID = order.frontID
                    req.sessionID = order.sessionID
                    req.orderID = order.orderID

                    reqList.append(req)

        self.mainEngine.batchCancelOrder(reqList, order.gatewayName)
        self.writeCtaLog('策略%s: 对本地订单%s，发送批量撤单委托，实际发送单量%s'%(order.byStrategy, vtOrderIDList,len(reqList)))

    #----------------------------------------------------------------------
    def sendStopOrder(self, vtSymbol, orderType, price, volume, priceType, strategy):
        """发停止单（本地实现）"""
        # 这里其实只是生成突破买入单并维护突破买入单 stopOrderID 与strategy 与 so 之间的映射关系
        self.stopOrderCount += 1
        stopOrderID = STOPORDERPREFIX + str(self.stopOrderCount)

        so = StopOrder()                                                                        #定义了本地停止单的结构体
        so.vtSymbol = vtSymbol
        so.orderType = orderType
        so.price = price
        so.priceType = priceType
        so.volume = volume                                                                     #这里的volume 同样也是正数
        so.strategy = strategy
        so.stopOrderID = stopOrderID
        so.status = STOPORDER_WAITING
        so.byStrategy = strategy.name

        if orderType == CTAORDER_BUY:
            so.direction = DIRECTION_LONG
            so.offset = OFFSET_OPEN
        elif orderType == CTAORDER_SELL:
            so.direction = DIRECTION_SHORT
            so.offset = OFFSET_CLOSE
        elif orderType == CTAORDER_SHORT:
            so.direction = DIRECTION_SHORT
            so.offset = OFFSET_OPEN
        elif orderType == CTAORDER_COVER:
            so.direction = DIRECTION_LONG
            so.offset = OFFSET_CLOSE
        # 以上完成了本地停止单的结构体的判定，接下来 ......
        # self.writeCtaLog("ctaenging sendStopOrder so.__dict__ 本地停止单结构%s "%(so.__dict__))
        """"
        这里我们可以参考，在sendorder之中我们做了哪些
        首先维护了个 reqlist 以及vtorderislist []
        通过循环判定在reqlist 之中请求的结构体的列表元素
            求出vtorderid
            定义了  vtorderid -----》strategy []
            定义了  startegy------->vtorderid
            分别将每个 vtorderid 添加到  vtOrderIDList.
        返回 vtOrderIDList.

        """
        # 这里做了映射  stopOrderID ------》订单结构数据
        self.stopOrderDict[stopOrderID] = so
        # 这里的stoporderid 是本地停止单的编号

        # self.writeCtaLog("ctaenging  sendStopOrder  self.stopOrderDict stopOrderID ------》%s "%(self.stopOrderDict))

        self.workingStopOrderDict[stopOrderID] = so                          #这个列表中数据可以删除
        # self.writeCtaLog("ctaenging  sendStopOrder self.stopOrderDict  workingStopOrderDic  stopOrderID ------》%s "%(self.workingStopOrderDict))

        # 维护 策略------》订单编号   #注意这里应该是加在最后才是正确的
        self.strategyOrderDict[strategy.name].add(stopOrderID)

        # 推送停止单状态
        strategy.onStopOrder(so)                                                      #可以进行精细化操作

        return [stopOrderID]


    #----------------------------------------------------------------------
    def cancelStopOrder(self, stopOrderID):
        """撤销停止单"""
        # 检查停止单是否存在
        if stopOrderID in self.workingStopOrderDict:
            so = self.workingStopOrderDict[stopOrderID]
            strategy = so.strategy

            # 更改停止单状态为已撤销
            so.status = STOPORDER_CANCELLED

            # 从活动停止单字典中移除
            del self.workingStopOrderDict[stopOrderID]

            # 从策略委托号集合中移除
            s = self.strategyOrderDict[strategy.name]
            if stopOrderID in s:
                s.remove(stopOrderID)

            # 通知策略              这里的通知策略是什么意思
            strategy.onStopOrder(so)

    #----------------------------------------------------------------------
    def processStopOrder(self, tick):
        """收到行情后处理本地停止单（检查是否要立即发出）"""
        vtSymbol = tick.vtSymbol

        # 首先检查是否有策略交易该合约
        if vtSymbol in self.tickStrategyDict:
            # 遍历等待中的停止单，检查是否会被触发  这一点很重要需要知道等待之中的停止单有哪些
            """
            等待之中的停止单有几种情况：
            1.没有仓位等待之中停止单
            2.有仓位止损等待之中的停止单

            """
            for so in list(self.workingStopOrderDict.values()):
                #这里根据每一个tickr 检查在sendstoporder之中维护的 workingstoporderdict ，当持有仓位的时候，比如说进行了3次买入则
                """
                循环逐个进行检查
                检查停止单列表 之中 发单结构体  <vnpy.trader.app.ctaStrategy.ctaBase.StopOrder object at 0x11a9a57b8>
                检查停止单列表 之中 发单结构体  <vnpy.trader.app.ctaStrategy.ctaBase.StopOrder object at 0x11a9a57f0>
                检查停止单列表 之中 发单结构体  <vnpy.trader.app.ctaStrategy.ctaBase.StopOrder object at 0x127a69828>

                """
                if so.vtSymbol == vtSymbol:               #如果在交易列表之中，这里的数据需要补充
                    #针对 每一个在workingstoporder 之中的发单，查看其中的详细信息

                    #这里设定了两个交易触发条件
                    #如果是多头方向 则为“买开” 或者 “卖平” 在这种状态下价格必须高于止损单的价格
                    longTriggered = so.direction==DIRECTION_LONG and tick.lastPrice>=so.price              # 多头停止单被触发

                    #如果是空头方向 则为“卖开” 或者 “买平” 在这种状态下价格必须高于止损单的价格
                    shortTriggered = so.direction==DIRECTION_SHORT and tick.lastPrice<=so.price           # 空头停止单被触发

                    #如果任何一个未真
                    if longTriggered or shortTriggered:

                        # 买入和卖出分别以涨停跌停价发单（模拟市价单）
                        # 对于没有涨跌停价格的市场则使用5档报价


                        if so.direction==DIRECTION_LONG:
                            self.writeCtaLog("ctaenging  processStopOrder 触发多头止损方向")
                            if tick.upperLimit:
                                price = tick.upperLimit
                            else:
                                price = tick.askPrice5
                        else:
                            self.writeCtaLog("ctaenging  processStopOrder 触发空头止损方向")
                            if tick.lowerLimit:
                                price = tick.lowerLimit
                            else:
                                price = tick.bidPrice5

                        # # 其中发现在是实盘之中经常因为滑点的原因，造成成交不了，现在进行滑点设置
                        # if so.direction==DIRECTION_LONG:
                        #     self.writeCtaLog("ctaenging  processStopOrder 触发多头止损方向")
                        #     if tick.upperLimit:
                        #         price = tick.upperLimit*0.99
                        #     else:
                        #         price = tick.askPrice5*0.99
                        # else:
                        #     self.writeCtaLog("ctaenging  processStopOrder 触发空头止损方向")
                        #     if tick.lowerLimit:
                        #         price = tick.lowerLimit*1.01
                        #     else:
                        #         price = tick.bidPrice5*1.01


                        #这里之前有个问题就是so.strategy 没有传入造成报错

                        strategy_ = so.strategy

                        # 发出市价委托   注意这里返回的是本地的订单编号 没有判断其是否成交
                        vtOrderID = self.sendOrder(so.vtSymbol, so.orderType, price, so.volume, so.priceType,strategy_)

                        #sendOrder(self, vtSymbol, orderType, price, volume, priceType, strategy):

                        # 检查因为风控流控等原因导致的委托失败（无委托号）
                        if vtOrderID:

                            # 从活动停止单字典中移除该停止单

                            del self.workingStopOrderDict[so.stopOrderID]

                            # 从策略委托号集合中移除       ？？？问题可能就是出现在这里
                            s = self.strategyOrderDict[so.strategy.name]
                            if so.stopOrderID in s:
                                s.remove(so.stopOrderID)

                            # 更新停止单状态，并通知策略
                            so.status = STOPORDER_TRIGGERED
                            so.strategy.onStopOrder(so)                      #这里通知策略是可以进行精华的

    #----------------------------------------------------------------------
    def processTickEvent(self, event):
        """处理行情推送"""
        tick = event.dict_['data']
        # 收到tick行情后，先处理本地停止单（检查是否要立即发出）
        self.processStopOrder(tick)

        # 推送tick到对应的策略实例进行处理
        if tick.vtSymbol in self.tickStrategyDict:
             #tick时间可能出现异常数据，使用try...except实现捕捉和过滤
            try:
                # 添加datetime字段
                if not tick.datetime:
                    tick.datetime = datetime.strptime(' '.join([tick.date, tick.time]), '%Y%m%d %H:%M:%S.%f')
            except ValueError:
                self.writeCtaLog(traceback.format_exc())
                return

            # 逐个推送到策略实例中
            l = self.tickStrategyDict[tick.vtSymbol]
            for strategy in l:
                if strategy.trading:
                    self.callStrategyFunc(strategy, strategy.onTick, tick)
                    if tick.datetime.second == 36 and tick.datetime.minute != self.minute_temp:
                        self.minute_temp = tick.datetime.minute
                        self.qryAllOrders(strategy.name)

    #----------------------------------------------------------------------
    def processOrderEvent(self, event):
        """处理委托推送
        由sendorder ---->vtenging---->gateway----->成交然后交易所回报--------》推送给策略进行细节控制
        通过事件监听回报数据，进行订单细化处理  还有对其中的监控的回报数据信息  这里需要判断的信息有很多

        """
        order = event.dict_['data']
        vtOrderID = order.vtOrderID                                       #这里的vtOrderID 就是维护的 发单的vtOrderID 交易所返回的信息

        #对于所有的本地的订单编号----》策略的映射，进行有效地判断
        if vtOrderID in self.orderStrategyDict:                                                          #如果为真 说明已经发单
            strategy = self.orderStrategyDict[vtOrderID]                                                 #针对每个订单 映射策略
            # 因为是cta 引擎所以循环查询所有策略
            if 'eveningDict' in strategy.syncList:                                                       #使用order 订单回报更新仓位
                #如果订单状态为 '已撤销'更新持仓
                if order.status == STATUS_CANCELLED:
                    if order.direction == DIRECTION_LONG and order.offset == OFFSET_CLOSE:
                        posName = order.vtSymbol + "_SHORT"
                        strategy.eveningDict[posName] += order.totalVolume - order.tradedVolume
                    elif order.direction == DIRECTION_SHORT and order.offset == OFFSET_CLOSE:
                        posName = order.vtSymbol + "_LONG"
                        strategy.eveningDict[posName] += order.totalVolume - order.tradedVolume

                #如果订单状态为 '部分成交'  '全部成交'   更新持仓    这里的情况是最多的，但是注意各个gateway 的区别
                elif order.status == STATUS_ALLTRADED or order.status == STATUS_PARTTRADED:
                    # 如果是 买开 状态
                    if order.direction == DIRECTION_LONG and order.offset == OFFSET_OPEN:
                        posName = order.vtSymbol + "_LONG"


                        # 这里定义了一个变量 本次交易量  thisTradedVolume  通过该变量更新仓位

                        strategy.eveningDict[posName] += order.thisTradedVolume
                    # 如果是 卖开 状态
                    elif order.direction == DIRECTION_SHORT and order.offset == OFFSET_OPEN:
                        posName = order.vtSymbol + "_SHORT"
                        strategy.eveningDict[posName] += order.thisTradedVolume

                #如果订单状态为 '未成交'
                elif order.status == STATUS_NOTTRADED:
                    if order.direction == DIRECTION_LONG and order.offset == OFFSET_CLOSE:
                        posName = order.vtSymbol + "_SHORT"
                        strategy.eveningDict[posName] -= order.totalVolume
                    elif order.direction == DIRECTION_SHORT and order.offset == OFFSET_CLOSE:
                        posName = order.vtSymbol + "_LONG"
                        strategy.eveningDict[posName] -= order.totalVolume


            # 如果委托已经完成（拒单、撤销、全成），则从活动委托集合中移除-----需要验证
            if order.status in STATUS_FINISHED:
                s = self.strategyOrderDict[strategy.name]


                if vtOrderID in s:
                    s.remove(vtOrderID)                                                         #如果还在列表之中将其删除掉
            self.callStrategyFunc(strategy, strategy.onOrder, order)                           #将监控到的gateway 信息推送给策略进行细节的控制


    #----------------------------------------------------------------------
    #这里的几个推送的维护，可以使用order 的推送也可以使用 trade 的推送，同样可以使用position 的推送
    def processTradeEvent(self, event):
        """处理成交推送"""
        trade = event.dict_['data']

        # # 过滤已经收到过的成交回报 这里将成交回报编号保留维护在字典之中
        if trade.vtTradeID in self.tradeSet:
            return
        self.tradeSet.add(trade.vtTradeID)

        # 将成交推送到策略对象中
        if trade.vtOrderID in self.orderStrategyDict:
            strategy = self.orderStrategyDict[trade.vtOrderID]


            # 计算策略持仓  我们是可以看到的在trade 的process 之中其实我们维护的信息是很少的，只是进行了仓位的维护，但是需要注意的是不同的
            # 交易所返回的trade 的数据是不一样的，所以有些是有 开 平的方向，在这种状态下，是可以进行仓位的判断的，其它的不能
            if trade.direction == DIRECTION_LONG and trade.offset == OFFSET_OPEN:
                posName = trade.vtSymbol + "_LONG"

                strategy.posDict[str(posName)] += trade.volume
            elif trade.direction == DIRECTION_LONG and trade.offset == OFFSET_CLOSE:
                posName = trade.vtSymbol + "_SHORT"
                strategy.posDict[str(posName)] -= trade.volume

            elif trade.direction ==DIRECTION_SHORT and trade.offset == OFFSET_CLOSE:
                posName = trade.vtSymbol + "_LONG"
                strategy.posDict[str(posName)] -= trade.volume
            elif trade.direction ==DIRECTION_SHORT and trade.offset == OFFSET_OPEN:
                posName = trade.vtSymbol + "_SHORT"
                strategy.posDict[str(posName)] += trade.volume
            self.callStrategyFunc(strategy, strategy.onTrade, trade)
    #----------------------------------
    def processPositionEvent(self, event):                   # nearly abandon
        """处理持仓推送"""
        pos = event.dict_['data']
        #可以看到这里的是针对每个策略进行仓位的更新的
        #根据cta-setting 之中的每个策略进行循环
        for strategy in self.strategyDict.values():
            # 注意这里的strategy 其实就是本次交易的策略比如说 turtule
            if strategy.inited and pos.vtSymbol in strategy.symbolList:            #这里其实就是自定义了交易对，对vnpy 的改造不仅支持单币种策略
                if pos.direction == DIRECTION_LONG:

                    posName = pos.vtSymbol + "_LONG"

                    strategy.posDict[str(posName)] = pos.position                  #这里定义了策略之中的posdict 的维护

                    strategy.eveningDict[str(posName)] = pos.position - pos.frozen


                    #这怒地ctp 策略这里进行单独的设定
                    if 'CTP' in posName:
                        self.ydPositionDict[str(posName)] = pos.ydPosition

                #这里要特别注意bitfinex 传过来pos 信息之中有没有frozen 值
                elif pos.direction == DIRECTION_SHORT:
                    self.writeCtaLog('processPositionEvent  pos direction short  %s' % (DIRECTION_SHORT))
                    posName2 = pos.vtSymbol + "_SHORT"
                    strategy.posDict[str(posName2)] = pos.position
                    strategy.eveningDict[str(posName2)] = pos.position - pos.frozen
                    if 'CTP' in posName2:
                        self.ydPositionDict[str(posName2)] = pos.ydPosition

                # 接下来是我在引擎之中添加的  当时是为了测试pos 其中在bitfinex 之中我默认地定义的pos_dic 是  DIRECTION_NET,当进行平仓操作之后，
                # 仓位变成此，仓位为 DIRECTION_NET，要进行策略的 pos 的维护需要进行重新定义
                elif pos.direction == DIRECTION_NET and pos.gatewayName == 'BITFINEX' :

                    if pos.position == 0:
                        strategy.eveningDict[str(pos.vtSymbol + "_SHORT")] = pos.position - pos.frozen
                        strategy.posDict[str(pos.vtSymbol + "_SHORT")] = pos.position
                        strategy.eveningDict[str(pos.vtSymbol + "_LONG")] = pos.position - pos.frozen
                        strategy.posDict[str(pos.vtSymbol + "_LONG")] = pos.position

                # 保存策略持仓到数据库,这里就是保留了策略的持仓到数据库
                self.saveSyncData(strategy)

                # 首先进行监听的是possition 之后才是进去监听order event

    #------------------------------------------------------
    def processAccountEvent(self,event):
        """账户推送"""
        account = event.dict_['data']
        for strategy in self.strategyDict.values():
            if strategy.inited:
                for sym in strategy.symbolList:
                    if account.gatewayName in sym:
                        strategy.accountDict[str(account.accountID)] = account.available
                        break


    def processErrorEvent(self,event):
        error = event.dict_['data']

        for strategy in self.strategyDict.values():
            if strategy.inited:
                for sym in strategy.symbolList:
                    if error.gatewayName in sym:
                        self.writeCtaLog(u'ProcessError，错误码：%s，错误信息：%s' %(error.errorID, error.errorMsg))        # 待扩展
                        return

    #--------------------------------------------------
    def registerEvent(self):
        """注册事件监听"""
        self.eventEngine.register(EVENT_TICK, self.processTickEvent)
        self.eventEngine.register(EVENT_POSITION, self.processPositionEvent)
        self.eventEngine.register(EVENT_ORDER, self.processOrderEvent)
        self.eventEngine.register(EVENT_TRADE, self.processTradeEvent)
        self.eventEngine.register(EVENT_ACCOUNT, self.processAccountEvent)
        self.eventEngine.register(EVENT_ERROR, self.processErrorEvent)

    #----------------------------------------------------------------------
    def insertData(self, dbName, collectionName, data):
        """插入数据到数据库（这里的data可以是VtTickData或者VtBarData）"""
        pass
        # for collectionName_ in collectionName:
        #     self.mainEngine.dbInsert(dbName, collectionName_, data.__dict__)

    #----------------------------------------------------------------------
    def loadBar(self, dbName, collectionName, hours):
        """从数据库中读取Bar数据，startDate是datetime对象"""
        pass
        # startDate = self.today - timedelta(hours = hours)
        # for collectionName_ in collectionName:
        #     d = {'datetime':{'$gte':startDate}}

        #     barData = self.mainEngine.dbQuery(dbName, collectionName_, d, 'datetime')

        #     l = []
        #     for d in barData:
        #         bar = VtBarData()
        #         bar.__dict__ = d
        #         bar.vtSymbol = collectionName_
        #         l.append(bar)
        #     return l

    #----------------------------------------------------------------------
    def loadTick(self, dbName, collectionName, hours):
        """从数据库中读取Tick数据，startDate是datetime对象"""
        pass
        # startDate = self.today - timedelta(hours = hours)
        # for collectionName_ in collectionName:

        #     d = {'datetime':{'$gte':startDate}}
        #     tickData = self.mainEngine.dbQuery(dbName, collectionName_, d, 'datetime')

        #     l = []
        #     for d in tickData:
        #         tick = VtTickData()
        #         tick.__dict__ = d
        #         l.append(tick)
        #     return l

    #----------------------------------------------------------------------
    def writeCtaLog(self, content):
        """快速发出CTA模块日志事件"""
        log = VtLogData()
        log.logContent = content
        log.gatewayName = 'CTA_STRATEGY'
        event = Event(type_=EVENT_CTA_LOG)
        event.dict_['data'] = log
        self.eventEngine.put(event)

    #----------------------------------------------------------------------
    def loadStrategy(self, setting):
        """载入策略"""
        try:
            name = setting['name']
            className = setting['className']
            vtSymbolset=setting['symbolList']
            mailAdd = setting['mailAdd']

        except Exception as e:
            self.writeCtaLog(u'载入策略%s出错：%s' %e)
            return

        # 获取策略类
        strategyClass = STRATEGY_CLASS.get(className, None)

        if not strategyClass:
            STRATEGY_GET_CLASS = self.loadLocalStrategy()
            strategyClass = STRATEGY_GET_CLASS.get(className, None)
            if not strategyClass:
                self.writeCtaLog(u'找不到策略类：%s' %className)
                return

        # 防止策略重名
        if name in self.strategyDict:
            self.writeCtaLog(u'策略实例重名：%s' %name)
        else:
            # 创建策略实例
            strategy = strategyClass(self, setting)
            self.strategyDict[name] = strategy
            strategy.symbolList = vtSymbolset
            strategy.mailAdd = mailAdd
            strategy.name = name
            d= {}
            fileName = os.path.join(self.path,strategy.name+'_syncData.json')
            if not os.path.exists(fileName):
                with open(fileName,'w') as f:
                    json.dump(d,f)
            self.loadSyncData(strategy)
            fileName = os.path.join(self.path,strategy.name+'_varData.json')
            if not os.path.exists(fileName):
                with open(fileName,'w') as f:
                    json.dump(d,f)
            fileName = os.path.join(self.path,strategy.name+'_orderSheet.json')
            if not os.path.exists(fileName):
                d['orders']=[]
                with open(fileName,'w') as f:
                    json.dump(d,f)

            # 创建委托号列表
            self.strategyOrderDict[name] = set()
            for vtSymbol in vtSymbolset :
                # 保存Tick映射关系
                if vtSymbol in self.tickStrategyDict:
                    l = self.tickStrategyDict[vtSymbol]
                else:
                    l = []
                    self.tickStrategyDict[vtSymbol] = l
                l.append(strategy)

    #-----------------------------------------------------------------------
    def subscribeMarketData(self, strategy):
        """订阅行情"""
        # 订阅合约
        for vtSymbol in strategy.symbolList:
            contract = self.mainEngine.getContract(vtSymbol)
            if contract:
                req = VtSubscribeReq()
                req.symbol = contract.symbol
                req.vtSymbol = contract.vtSymbol
                req.exchange = contract.exchange

                # 对于IB接口订阅行情时所需的货币和产品类型，从策略属性中获取
                req.currency = strategy.currency
                req.productClass = strategy.productClass

                self.mainEngine.subscribe(req, contract.gatewayName)
            else:
                self.writeCtaLog(u'策略%s的交易合约%s无法找到' %(strategy.name, vtSymbol))

    #----------------------------------------------------------------------
    def initStrategy(self, name):                                      #这就是初始化策略的在交易界面上的策略
        """初始化策略"""
        if name in self.strategyDict:
            strategy = self.strategyDict[name]

            if not strategy.inited:
                strategy.inited = True
                self.initPosition(strategy)                             #这里是加载初始化的仓位的变化
                self.callStrategyFunc(strategy, strategy.onInit)
                self.subscribeMarketData(strategy)                      # 加载同步数据后再订阅行情

                self.writeCtaLog(u'策略%s： 初始化' %name)

            else:
                self.writeCtaLog(u'请勿重复初始化策略实例：%s' %name)
        else:
            self.writeCtaLog(u'策略实例不存在：%s' %name)

    #---------------------------------------------------------------------
    def startStrategy(self, name):
        """启动策略"""
        if name in self.strategyDict:
            strategy = self.strategyDict[name]

            if strategy.inited and not strategy.trading:
                strategy.trading = True
                self.callStrategyFunc(strategy, strategy.onStart)
                self.writeCtaLog(u'策略%s： 启动' %name)
        else:
            self.writeCtaLog(u'策略实例不存在：%s' %name)

    #----------------------------------------------------------------------
    def stopStrategy(self, name):
        """停止策略"""
        if name in self.strategyDict:
            strategy = self.strategyDict[name]

            if strategy.trading:
                self.writeCtaLog(u'策略%s： 准备停止工作' %name)
                self.saveVarData(strategy)
                strategy.trading = False
                self.callStrategyFunc(strategy, strategy.onStop)

                # 对该策略发出的所有限价单进行撤单
                for vtOrderID, s in list(self.orderStrategyDict.items()):
                    if s is strategy:
                        self.cancelOrder(vtOrderID)

                # 对该策略发出的所有本地停止单撤单
                for stopOrderID, so in list(self.workingStopOrderDict.items()):
                    if so.strategy is strategy:
                        self.cancelStopOrder(stopOrderID)

            strategy.inited = False  ## 取消注释使策略在停止后可以再次初始化
            self.writeCtaLog(u'策略%s： 停止工作' %name)
            ## 加上删除持仓信息
        else:
            self.writeCtaLog(u'策略实例不存在：%s' %name)

    #----------------------------------------------------------------------
    def initAll(self):
        """全部初始化"""
        for name in list(self.strategyDict.keys()):
            self.initStrategy(name)

    #----------------------------------------------------------------------
    def startAll(self):
        """全部启动"""
        for name in list(self.strategyDict.keys()):
            self.startStrategy(name)

    #----------------------------------------------------------------------
    def stopAll(self):
        """全部停止"""
        for name in list(self.strategyDict.keys()):
            self.stopStrategy(name)

    #----------------------------------------------------------------------
    def saveSetting(self):
        """保存策略配置"""
        with open(self.settingfilePath, 'w') as f:
            l = []

            for strategy in list(self.strategyDict.values()):
                setting = {}
                for param in strategy.paramList:
                    setting[param] = strategy.__getattribute__(param)
                l.append(setting)

            jsonL = json.dumps(l, indent=4)
            f.write(jsonL)

    #----------------------------------------------------------------------
    def loadSetting(self):
        """读取策略配置"""
        with open(self.settingfilePath) as f:
            l = json.load(f)

            for setting in l:

                if 'policy' in setting.keys():
                    POLICY_CLASS  = {}
                    if setting['policy']:
                        POLICY_CLASS = self.loadPolicy(setting['policy'])
                        policyClass = POLICY_CLASS.get(setting['policy'], None)
                        if not policyClass:
                            self.writeCtaLog(u'找不到Policy：%s' %setting['policy'])
                            return
                        newsetting = policyClass(setting)
                        newsetting.assert_symbol()
                        print(newsetting.setting)
                        self.loadStrategy(newsetting.setting)
                        continue

                self.loadStrategy(setting)

        # for strategy in self.strategyDict.values():
        #     self.loadSyncData(strategy)

    #----------------------------------------------------------------------
    def getStrategyVar(self, name):
        """获取策略当前的变量字典"""
        if name in self.strategyDict:
            strategy = self.strategyDict[name]
            varDict = OrderedDict()

            for key in strategy.varList:
                varDict[key] = strategy.__getattribute__(key)

            return varDict
        else:
            self.writeCtaLog(u'策略实例不存在：' + name)
            return None

    #----------------------------------------------------------------------
    def getStrategyParam(self, name):
        """获取策略的参数字典"""
        if name in self.strategyDict:
            strategy = self.strategyDict[name]
            paramDict = OrderedDict()

            for key in strategy.paramList:
                paramDict[key] = strategy.__getattribute__(key)

            return paramDict
        else:
            self.writeCtaLog(u'策略实例不存在：' + name)
            return None
    #-----------------------------------
    def getStrategyNames(self):
        """查询所有策略名称"""
        return self.strategyDict.keys()
    #----------------------------------------------------------------------
    def putStrategyEvent(self, name):
        """触发策略状态变化事件（通常用于通知GUI更新）"""
        strategy = self.strategyDict[name]
        d = {k:strategy.__getattribute__(k) for k in strategy.varList}

        event = Event(EVENT_CTA_STRATEGY+name)
        event.dict_['data'] = d
        self.eventEngine.put(event)

        d2 = {k:str(v) for k,v in d.items()}
        d2['name'] = name
        event2 = Event(EVENT_CTA_STRATEGY)
        event2.dict_['data'] = d2
        self.eventEngine.put(event2)

    #----------------------------------------------------------------------
    def callStrategyFunc(self, strategy, func, params=None):
        """调用策略的函数，若触发异常则捕捉"""
        try:
            if params:
                func(params)
            else:
                func()
        except Exception:
            # 停止策略，修改状态为未初始化
            self.stopStrategy(strategy.name)
            content = '\n'.join([u'策略%s：触发异常, 当前状态已保存, 挂单将全部撤销' %strategy.name,
                                traceback.format_exc()])

            mail(content,strategy)
            self.writeCtaLog(content)

    #----------------------------------------------------------------------------------------
    def saveSyncData(self, strategy):    #改为posDict
        """保存策略的持仓情况到数据库"""

        flt = {'name': strategy.name,
            'subject':str(strategy.symbolList)}
        # result = []
        d = {}
        for key in strategy.syncList:
            d[key] = strategy.__getattribute__(key)
            # result.append(key)
            # result.append(d[key])

        flt['SyncData'] = d
        fileName = os.path.join(self.path, strategy.name + '_syncData.json')
        with open(fileName,'w') as f:
            json.dump(flt,f,indent=4, ensure_ascii=False)
        # self.mainEngine.dbUpdate(POSITION_DB_NAME, strategy.name,
        #                             d, flt, True)

        # content = u'策略%s: 同步数据保存成功,当前仓位状态:%s' %(strategy.name,result)
        # self.writeCtaLog(content)

    def saveVarData(self, strategy):
        flt = {'name': strategy.name,
            'subject':str(strategy.symbolList)}
        # result = []
        d = {}
        for key in strategy.varList:
            d[key] = strategy.__getattribute__(key)
            # result.append(key)
            # result.append(d[key])

        flt['VarData'] = d

        fileName = os.path.join(self.path, strategy.name + '_varData.json')
        with open(fileName,'w') as f:
            json.dump(flt,f,indent=4, ensure_ascii=False)

        # self.mainEngine.dbUpdate(VAR_DB_NAME, strategy.name,
        #                             d, flt, True)

        # content = u'策略%s: 参数数据保存成功,参数为%s' %(strategy.name,result)
        # self.writeCtaLog(content)

    #----------------------------------------------------------------------
    def loadSyncData(self, strategy):
        """从数据库载入策略的持仓情况"""
        fileName = os.path.join(self.path, strategy.name + '_syncData.json')
        with open(fileName,'r') as f:
            syncData = json.load(f)

        # flt = {'name': strategy.name,
        # 'posName': str(strategy.symbolList)}
        # syncData = self.mainEngine.dbQuery(POSITION_DB_NAME, strategy.name, flt)

        if not syncData:
            self.writeCtaLog(u'策略%s: 当前没有持仓信息'%strategy.name)
            return
        for sym in strategy.symbolList:
            if sym not in syncData['subject']:
                self.writeCtaLog(u'策略%s: 当前SyncData不属于此策略'%strategy.name)
                return

        d = syncData['SyncData']
        for key in strategy.syncList:
            if key in d:
                strategy.__setattr__(key, d[key])

    def loadVarData(self, strategy):
        """从数据库载入策略的持仓情况"""
        fileName = os.path.join(self.path, strategy.name + '_varData.json')
        with open(fileName,'r') as f:
            varData = json.load(f)

        # flt = {'name': strategy.name,
        # 'posName': str(strategy.symbolList)}
        # varData = self.mainEngine.dbQuery(VAR_DB_NAME, strategy.name, flt)

        if not varData:
            self.writeCtaLog(u'策略%s: 当前没有保存的变量信息'%strategy.name)
            return

        for sym in strategy.symbolList:
            if sym not in varData['subject']:
                self.writeCtaLog(u'策略%s: 当前varData不属于此策略'%strategy.name)
                return

        d = varData['VarData']
        for key in strategy.varList:
            if key in d:
                strategy.__setattr__(key, d[key])

    def saveOrderDetail(self, strategy, order):
        """
        将订单信息存入数据库
        """
        flt = {'name': strategy.name,
            'vtOrderID':order.vtOrderID,
            'symbol':order.vtSymbol,
            'exchageID': order.exchangeOrderID,
            'direction':order.direction,
            'offset':order.offset,
            'price': order.price,
            'price_avg': order.price_avg,
            'tradedVolume':order.tradedVolume,
            'totalVolume':order.totalVolume,
            'status':order.status,
            'orderby':order.byStrategy
            }
        if order.deliverTime:
            flt['orderTime'] = order.deliverTime.strftime('%Y%m%d %X')
        fileName = os.path.join(self.path, strategy.name + '_orderSheet.json')
        with open(fileName,'r') as f:
            data = json.load(f)
            data['orders'].append(flt)

        with open(fileName,'w') as f:
            json.dump(data,f,indent=4, ensure_ascii=False)

        # self.mainEngine.dbInsert(ORDER_DB_NAME, strategy.name, flt)
        content = u'策略%s: 保存%s订单数据成功，本地订单号%s' %(strategy.name, order.vtSymbol, order.vtOrderID)
        self.writeCtaLog(content)

    #----------------------------------------------------------------------
    def roundToPriceTick(self, priceTick, price):
        """取整价格到合约最小价格变动"""
        d = Decimal(str(price))
        newPrice = float(d.quantize(Decimal(str(priceTick))))
        return newPrice



    #----------------------------------------------------------------------
    def stop(self):
        """停止"""
        pass

    #----------------------------------------------------------------------
    def cancelAll(self, name):
        """全部撤单"""
        s = self.strategyOrderDict[name]
        print("ctaenging cancleall s = self.strategyOrderDict[name]", s)
        """
        set()                         #第一次取消cancleall 是空的值
        ctaenging cancleall s = self.strategyOrderDict[name] {'CtaStopOrder.2', 'CtaStopOrder.1'}

        """
        # 遍历列表，查找非停止单全部撤单
        # 这里不能直接遍历集合s，因为撤单时会修改s中的内容，导致出错
        for orderID in list(s):
            print("ctaenging cancleall list(s)", list(s))
            # ctaenging cancleall list(s)           ['CtaStopOrder.2', 'CtaStopOrder.1']
            """
         ['CtaStopOrder.17', 'CtaStopOrder.1', 'CtaStopOrder.14', 'BITFINEX:1554831038', 'CtaStopOrder.58', 'BITFINEX:1554831035',
         'BITFINEX:1554831042', 'BITFINEX:1554831036', 'CtaStopOrder.56', 'CtaStopOrder.21', 'BITFINEX:1554831037', 'CtaStopOrder.55',
          'BITFINEX:1554831032', 'BITFINEX:1554831048', 'CtaStopOrder.28', 'CtaStopOrder.22', 'BITFINEX:1554831046', 'CtaStopOrder.59',
           'BITFINEX:1554831049', 'BITFINEX:1554831050', 'CtaStopOrder.2', 'CtaStopOrder.24', 'BITFINEX:1554831041', 'BITFINEX:1554831047',
           'BITFINEX:1554831051', 'CtaStopOrder.3', 'CtaStopOrder.60', 'BITFINEX:1554831034', 'CtaStopOrder.23', 'CtaStopOrder.19',
           'CtaStopOrder.4', 'CtaStopOrder.29', 'BITFINEX:1554831045', 'CtaStopOrder.54', 'BITFINEX:1554831043', 'CtaStopOrder.20',
           'BITFINEX:1554831040', 'CtaStopOrder.57', 'CtaStopOrder.27', 'CtaStopOrder.18', 'CtaStopOrder.15', 'BITFINEX:1554831044',
           'CtaStopOrder.25', 'CtaStopOrder.16', 'BITFINEX:1554831033', 'BITFINEX:1554831039', 'CtaStopOrder.30', 'CtaStopOrder.26']

            """

            if STOPORDERPREFIX not in orderID:      #将本地发出的订单全部撤销， 即取消 BITFINEX:1554831033 这种格式的订单
                self.cancelOrder(orderID)
            """
            # 本地停止单前缀
            STOPORDERPREFIX = 'CtaStopOrder.'
            """


    def cancelAllStopOrder(self,name):
        """撤销所有停止单"""
        s= self.strategyOrderDict[name]
        for orderID in list(s):
            if STOPORDERPREFIX in orderID:         # 看到了他们之间的区别了没有，这里是停止单的处理
                self.cancelStopOrder(orderID)

    #----------------------------------------------------------------------
    def getPriceTick(self, strategy):
        """获取最小价格变动"""

        for vtSymbol in strategy.symbolList:
            contract = self.mainEngine.getContract(vtSymbol)
            if contract:
                return contract.priceTick
            return 0

    #--------------------------------------------------------------
    # 加载的历史数据，所有的交易的引擎都是从底层进行的维护
    def loadHistoryBar(self,vtSymbol,type_,size = None,since = None):
        """读取历史数据"""
        data = self.mainEngine.loadHistoryBar(vtSymbol, type_, size, since)
        histbar = []
        for index, row in data.iterrows():
            bar = VtBarData()
            bar.open = row.open
            bar.close = row.close
            bar.high = row.high
            bar.low = row.low
            bar.volume = row.volume
            bar.vtSymbol = vtSymbol
            bar.datetime = row.datetime
            histbar.append(bar)
        return histbar

    #--------------------------------------------------------------
    # 加载的历史数据，所有的交易的引擎都是从底层进行的维护
    def loadKindleBar(self,vtSymbol,type_,size = None,since = None):
        """读取历史数据"""
        data = self.mainEngine.loadKindleBar(vtSymbol, type_, size, since)
        # histbar = []
        # for index, row in data.iterrows():
        #     bar = VtBarData()
        #     bar.open = row.open
        #     bar.close = row.close
        #     bar.high = row.high
        #     bar.low = row.low
        #     bar.volume = row.volume
        #     bar.vtSymbol = vtSymbol
        #     bar.datetime = row.datetime
        #     histbar.append(bar)
        # return histbar
        return data


    # 重新设计的引擎用来维护策略之中的持仓，保障在持有仓位的状态下，重新启动程序有相关的仓位
    def initPosition(self,strategy):
        for i in range(len(strategy.symbolList)):
            symbol = strategy.symbolList[i]
            if 'posDict' in strategy.syncList:
                strategy.posDict[symbol+"_LONG"] = 0
                strategy.posDict[symbol+"_SHORT"] = 0
            if 'eveningDict' in strategy.syncList:
                strategy.eveningDict[symbol+"_LONG"] = 0
                strategy.eveningDict[symbol+"_SHORT"] = 0

        # 根据策略的品种信息，查询特定交易所该品种的持仓 到底层的vtgateway 之中的接口进行查询仓位
        for vtSymbol in strategy.symbolList:
            self.mainEngine.initPosition(vtSymbol)

    def qryAllOrders(self,name):

        if name in self.strategyDict:
            strategy = self.strategyDict[name]
            s = self.strategyOrderDict[name]
            for symbol in strategy.symbolList:
                self.mainEngine.qryAllOrders(symbol, -1, status = 1)
                # self.writeCtaLog("ctaEngine对策略%s发出%s的挂单轮询请求，本地订单数量%s"%(name,symbol,len(list(s))))

    def restoreStrategy(self, name):
        """恢复策略"""
        if name in self.strategyDict:
            strategy = self.strategyDict[name]

            if not strategy.inited and not strategy.trading:
                strategy.inited = True
                strategy.trading = True

                self.callStrategyFunc(strategy, strategy.onRestore)
                self.loadVarData(strategy)            # 初始化完成后加载同步数据
                self.loadSyncData(strategy)
                self.writeCtaLog(u'策略%s： 恢复策略状态成功' %name)

            else:
                self.writeCtaLog(u'策略%s： 策略无法从当前状态恢复' %name)
        else:
            self.writeCtaLog(u'策略实例不存在：%s' %name)

    def loadLocalStrategy(self):
        # 用来保存策略类的字典
        STRATEGY_GET_CLASS = {}

        # 获取目录路径， 遍历当前目录下的文件
        path = os.getcwd()

        for root, subdirs, files in os.walk(path):
            for name in files:
                # 只有文件名中包含strategy且非.pyc的文件，才是策略文件
                if 'Strategy' in name and '.pyc' not in name:
                    # 模块名称需要上前缀
                    moduleName = name.replace('.py', '')

                    # 使用importlib动态载入模块
                    try:
                        module = importlib.import_module(moduleName)

                        # 遍历模块下的对象，只有名称中包含'Strategy'的才是策略类
                        for k in dir(module):
                            if 'Strategy' in k:
                                v = module.__getattribute__(k)
                                STRATEGY_GET_CLASS[k] = v

                    except:
                        print('-' * 20)
                        print(('Failed to import strategy file %s:' %moduleName))
                        traceback.print_exc()

        return STRATEGY_GET_CLASS

    def getGateway(self, gatewayName):
        return self.mainEngine.gatewayDict.get(gatewayName, None)

    def loadPolicy(self,policyName):
        POLICY_CLASS ={}
        if os.path.exists('policy.py'):
            try:
                module = importlib.import_module('policy')
                for k in dir(module):
                    if policyName in k:
                        v = module.__getattribute__(k)
                        POLICY_CLASS[k] = v
            except:
                print('-' * 20)
                print(('Failed to import policy file'))
                traceback.print_exc()
        return POLICY_CLASS
