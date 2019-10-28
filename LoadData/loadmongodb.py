# encoding: UTF-8
"""
导入mongodn导出来历史数据到MongoDB中，这里使用数据服务进行数据的搜集
注意导入的数据的格式以及导入的数据存储的位置，导入数据的文件名,根据不同数据未知进行匹配
"""
import sys
import csv
from datetime import datetime, timedelta
from time import time
import pymongo
from vnpy.trader.app.ctaStrategy.ctaBase import SETTING_DB_NAME, TICK_DB_NAME, MINUTE_DB_NAME, DAILY_DB_NAME
from vnpy.trader.vtGlobal import globalSetting
from vnpy.trader.vtObject import VtBarData
from vnpy.trader.vtConstant import *
"""
导入的数据格式示例：

	gatewayName	 rawData	vtSymbol	    symbol	exchange	open	high	       low	  close	    date	    time	datetime	   volume	    openInterest	amount
	BITFINEX		     ETHUSD:BITFINEX	ETHUSD	BITFINEX	207.91	207.98158	207.44	  207.57	20190514	15:14	2019/5/14 15:14	270.4325735	  0	     0
	BITFINEX		     ETHUSD:BITFINEX	ETHUSD	BITFINEX	207.5	207.51	    207.04	  207.04	20190514	15:15	2019/5/14 15:15	46.47626004	  0	     0


1          2           3         4       5            6            7           8        9      10          11           12         13        14       15        16
"_id",   "amount",   "close",  "date", "datetime", "exchange", "gatewayName", "high",  "low","open", "openInterest", "rawData",  "symbol", "time",  "volume", vtSymbol"
"5ce8663e86deb57676dc5642","0","8.0441","20170101","2017/1/1 00:04:00","BITFINEX","BITFINEX","8.0441","8.0441","8.0441","0",,"ETHUSD","00:04","10.249044","ETHUSD:BITFINEX"
"5ce8663e86deb57676dc5644","0","8.1202","20170101","2017/1/1 00:13:00","BITFINEX","BITFINEX","8.1202","8.1179","8.1179","0",,"ETHUSD","00:13","10.221333","ETHUSD:BITFINEX"
"5ce8663e86deb57676dc5646","0","8.1842","20170101","2017/1/1 00:14:00","BITFINEX","BITFINEX","8.1842","8.1717","8.1717","0",,"ETHUSD","00:14","64.6369","ETHUSD:BITFINEX"

"""

def loadCoinCsv(fileName, dbName, symbol):                                                     #根据后边的示例symbol 的格式必须为 EOSUSD:BITFINEX
    """将OKEX导出的csv格式的历史分钟数据插入到Mongo数据库中"""
    start = time()
    print('开始读取CSV文件%s中的数据插入到%s的%s中' %(fileName, dbName, symbol))                    #这里我们可以看到的是 symbol 与 filename 是一样的格式

    # 锁定集合，并创建索引
    #——————————————————% 这里可以尝试搭建属于自己的数据库服务器，进行数据服务的采集与操作————————————————————————————————————————————————————————
    client = pymongo.MongoClient(globalSetting['mongoHost'], globalSetting['mongoPort'])
    collection = client[dbName][symbol]                                                         #所以识别还是通过symbol 来进行识别的
    collection.ensure_index([('datetime', pymongo.ASCENDING)], unique=True)                     #创建数据库索引，并且是以时间作为索引的

    # 读取数据和插入到数据库
    reader = csv.reader(open(fileName,"r"))                                                     #读取数据

    for d in reader:
        if len(d[0]) >10:
            bar = VtBarData()
            bar.vtSymbol = symbol
            bar.symbol, bar.exchange = symbol.split(':')                                        #这里设置了vt symbol 的数据格式，这里将symbol 进行了切片处理
            bar.gatewayName = bar.exchange

            # #注意区域数据的时间的格式，按照下面的时间格式
            # bar.datetime = datetime.strptime(d[12], '%Y/%m/%d %H:%M:%S')                            #将字符串转换成时间格式，转换成的时间格式 '%Y/%m/%d %H:%M'
            # bar.date = bar.datetime.date().strftime('%Y%m%d')                                   #将日期的时间格式的 天转换成 字符串格式 格式  '%Y%m%d'
            # bar.time = bar.datetime.time().strftime('%H:%M')
            #
            # bar.high = float(d[7])                                                          # 注意这里是的所有的额 开 平 收 高 低 都是 float
            # bar.low = float(d[8])
            # bar.open = float(d[6])
            # bar.close = float(d[9])
            #
            # bar.amount = float(d[15])                                                        #这里主要是还是指 成交的金额
            # bar.volume = float(d[13])                                                        #就是成交量  数值一般是比较地小，回测过程之中只有对成交量进行了更新


            #注意区域数据的时间的格式，按照下面的时间格式
            bar.datetime = datetime.strptime(d[4], '%Y/%m/%d %H:%M:%S')                            #将字符串转换成时间格式，转换成的时间格式 '%Y/%m/%d %H:%M'
            bar.date = bar.datetime.date().strftime('%Y%m%d')                                   #将日期的时间格式的 天转换成 字符串格式 格式  '%Y%m%d'
            bar.time = bar.datetime.time().strftime('%H:%M')

            bar.high = float(d[7])                                                          # 注意这里是的所有的额 开 平 收 高 低 都是 float
            bar.low = float(d[8])
            bar.open = float(d[9])
            bar.close = float(d[2])

            bar.amount = float(d[1])                                                        #这里主要是还是指 成交的金额
            bar.volume = float(d[14])                                                        #就是成交量  数值一般是比较地小，回测过程之中只有对成交量进行了更新


            flt = {'datetime': bar.datetime}
            collection.update_one(flt, {'$set':bar.__dict__}, upsert=True)                      #这里使用了更新，update 进行更新插入操作
            print('%s \t %s' % (bar.date, bar.time))
    print('插入完毕，耗时：%s' % (time()-start))

if __name__ == '__main__':
    #+++++++++++=主要是bitfinex 数据的读取操作+++++++++++++++++++++++++++++++++++++++

    loadCoinCsv('ETHUSD_BITFINEX.csv', MINUTE_DB_NAME, 'ETHUSD:BITFINEX')                      #注意这里的是ETHUSD:BITFINEX 就是vtsymbol

