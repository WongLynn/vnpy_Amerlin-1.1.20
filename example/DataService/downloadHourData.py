# encoding: UTF-8

"""
该程序主要是从matrixdata 之中获取交易所相关的数据信息
madeby；yalinwang
date:2018-11-12
QQ:894510791
该文件为数据源文件sdk，文件格式为数据源原始文件格式   数字货币市场bitfinex 的数据是从 2013年4月开始的
该数据下载服务主要是针对海龟交易策略之中的进行目前主要的针对1 h 海龟交易策略进行数据的下载服务
"""
from __future__ import print_function
import json
import time
import datetime
import requests

# from dataService import *

#===================================================================================================pandas 显示先关的定义
import pandas as pd
pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', None)
pd.set_option('expand_frame_repr', False)                                                              # 当列太多时不换行

from vnpy.trader.vtObject import VtBarData
from vnpy.trader.app.ctaStrategy.ctaBase import MINUTE_DB_NAME,HOUR_DB_NAME                       #仅仅引入分钟级别的数据库
from pymongo import MongoClient, ASCENDING



# 加载配置
config = open('config.json')
setting = json.load(config)

MONGO_HOST = setting['MONGO_HOST']
MONGO_PORT = setting['MONGO_PORT']
APIKEY = setting['APIKEY']
SYMBOLS = setting['SYMBOLS']

mc = MongoClient(MONGO_HOST, MONGO_PORT)                         # Mongo连接
# db = mc[MINUTE_DB_NAME]                                          # 数据库,该数据库下的
db = mc[HOUR_DB_NAME]                                          # 数据库,该数据库下的



# ========================================================SYMBOL 的转换 注意这里将symbol 转换成为vtsymbol 即进行过数据库读写的
def symbol_get(symbol):
    if symbol:
        print(symbol)

    l = symbol.split('/')             #[BTC, USD.BF]
    m = l[-1]                         # USD.BF
    n = m.split('.')                  # [USD,BF]
    bar_symbol = l[0] + n[0]          # BTCUSD             #之前的symbol 是BTCUSD:BITFINEX 根据加载历史数据的函数现在是
    if n[-1] == 'BF':
        bar_exchange = 'BITFINEX'
        vtSymbol = ':'.join([bar_symbol , bar_exchange])    #这里是正确的，在进行数据回测之中读取的是
        return vtSymbol


#==============================================================================================生成交易的k 线系列  这里是关键可以适配做种交易所数据
def generateVtBar(symbol, d):                                    #传入交易对以及每条记录信息   "BTC/USD.BF"
    """生成K线"""
    # 这里将在maxdata service 服务之中交易对信息转化成vnpy 之中的交易对信息

    l = symbol.split('/')             #[BTC, USD.BF]
    m = l[-1]                         # USD.BF
    n = m.split('.')                  # [USD,BF]
    bar = VtBarData()
    bar.symbol = l[0] + n[0]          # BTCUSD             #之前的symbol 是BTCUSD:BITFINEX 根据加载历史数据的函数现在是
    bar.exchange = 'BITFINEX'
    bar.gatewayName = 'BITFINEX'
    bar.vtSymbol = ':'.join([bar.symbol, bar.exchange])    #这里是正确的，在进行数据回测之中读取的是

    #===============================对数据库文件格式进行处理==================================
    # 目标 'time', 'open', 'high', 'low', 'close', 'volume', 'amount'  #注意这里的volume 是重点，后边的所有的计算斗志通过volume 进行计算的
    """
     #源数据 d {'Symbol': 'BTC/USD.BF',
                'Time': '2017-10-04T10:00:00.000Z',
                'Open': '4248.2',
                'High': '4250',
                'Low': '4218.3',
                'Close': '4245',
                 QuoteVolume': '1512.60001705',               #基础币种成交量  就是volume  重点关注这个
                'QuoteAssetVolume': '0',                      #计价币钟成交量  就是交易额
                'TradeNum': '0'
            }
    """
    # 注意因为后边涉及到相关的计算，这里所有的包含数值的项目都是float 进行计算    参考海龟交易之中的根据原版之中的loadcsv 进行的处理

    bar.datetime = datetime.datetime.strptime(d['Time'],'%Y-%m-%dT%H:%M:%S.%f0Z')                    #js 匹配字符串格式，转换成时间格式
    bar.datetime = bar.datetime.strftime('%Y/%m/%d %H:%M')                                 #将时间格式转化成字符串格式，匹配输出自己想要的精准到小时
    # print("bar.datetime",bar.datetime)
    bar.datetime = datetime.datetime.strptime(bar.datetime,'%Y/%m/%d %H:%M')                         #再将字符串格式转换成 日期格式 精准到小时

    bar.open = float(d['Open'])                                    #对应生成
    bar.high = float(d['High'])                                    #对应生成
    bar.low = float(d['Low'])                                      #对应生成
    bar.close = float(d['Close'])                                  #对应生成
    bar.volume = float(d['QuoteVolume'])                           #对应生成
    bar.amount = float(d['QuoteAssetVolume'])                      #对应生成
    bar.date = bar.datetime.date().strftime('%Y%m%d')              #时间转字符串  注意对应loadcsv 文件的转换
    bar.time = bar.datetime.time().strftime('%H:%M')              #时间转字符串  注意对应loadcsv 文件的转换精准到小时

    return bar


# 拼接url
def parse_params_to_str(params):                                                                      # 将参数转换成字符串
    url = '?'
    for key, value in params.items():
        url = url + str(key) + '=' + str(value) + '&'
        # print("url[0:-1]",url[0:-1])
    return url[0:-1]


# 将返回的json 文件转换成dataFrame 格式基本信息
def json2dataframe(data):
    if data['Head']['Code'] == '200':                                                                 #返回正确的代码
        df = pd.DataFrame.from_records(data['Result'])
        return df
    else:
        print ('error! get data',data['Head']['Code'])                                                #返回错的代码






# 定义 matrixdata_sdk  数据的类，包含多种获取的方式 注意其中的token是有过期的时间
class matrixdata_sdk():
    def __init__(self,token = APIKEY,debug = False):
        self.headers = headers = {"Authorization": token, "Content-type": "application/json"}
        self.debug = debug

    # 定义request url 的方式
    def request_get(self,url):
        if self.debug:
            print (url)
        for i in range(10):                                                                          #多次容错请求
            try:
                response = requests.get(url, headers=self.headers)
                result = response.json()                                                             #rest 请求返回json 格式
                return result
            except:
                print ('get data failed try it again! ' + str(i) + ' times')
                pass

    def downMinuteBarBySymbol(self, params):
        """
        所有的json 导入的数据格式都是str
        params = {'symbol': symbol,                                           #注意这里的symbol 是maxdata 的数据格式
          'interval': '1h',
          'start': '2017-10-01 10:01:00',
          'end': '2018-05-31 00:00:00',
          }
        :param params:
        :return:
        """
        # print("params",params)
        symbol = params['symbol']                                                 # TC/USD.BF
        # 根据实践情况，这里数据库的表空间因该是vtsymbol 所以进行转化
        _symbol = symbol_get(symbol)

        startTime = time.time()                                                   # 1557571175.43439
        # 注意这里的数据库的表空间
        cl = db[_symbol]                                                           # 即定义的数据库1h 数据库表空间
        cl.ensure_index([('datetime', ASCENDING)], unique=True)                   # cl.ensure_index([("索引字段名", 1)], unique=True)

        url = "https://api.matrixdata.io/matrixdata/api/v1/barchart" + parse_params_to_str(params)
        result = self.request_get(url)
        print('result', result)                                                   #resut 返回json 格式
        """
            {'Head': {'Message': 'Success', 'Code': '200', 'CallTime': '20190511113422'},
            'Result': [
                        {'Symbol': 'BTC/USD.BF', 'Time': '2017-10-01T11:00:00.000Z', 'Open': '4325.9', 'High': '4330', 'Low': '4311.1',
                            'Close': '4312.1', 'QuoteVolume': '625.37708273', 'QuoteAssetVolume': '0', 'TradeNum': '0'},
                        {'Symbol': 'BTC/USD.BF', 'Time': '2017-10-22T04:00:00.000Z', 'Open': '5940.9', 'High': '5940.9',
                            'Low': '5733.3', 'Close': '5757.9', 'QuoteVolume': '7445.57966636', 'QuoteAssetVolume': '0', 'TradeNum': '0'},
                        {'Symbol': 'BTC/USD.BF', 'Time': '2017-10-22T05:00:00.000Z', 'Open': '5758.3', 'High': '5899.9', 'Low': '5745',
                                'Close': '5850', 'QuoteVolume': '3136.81243258', 'QuoteAssetVolume': '0', 'TradeNum': '0'},
                        {'Symbol': 'BTC/USD.BF', 'Time': '2017-10-22T06:00:00.000Z', 'Open': '5850', 'High': '5878.6', 'Low': '5777.3',
                            'Close': '5875.5', 'QuoteVolume': '1449.52483682', 'QuoteAssetVolume': '0', 'TradeNum': '0'}
                        ]
            }
        """
        # ===================== 接下来的一部分是将其写入数据库之中  进行数据的处理将返回的数据截取成数组
        resp = result['Result']
        l = resp
        for d in l:
            bar = generateVtBar(symbol, d)                                         # 将每条数据灌进去生成bar,注意这里的symbol 还是maxdata
            d = bar.__dict__
            flt = {'datetime': bar.datetime}
            cl.replace_one(flt, d, True)                                           # replaceOne()这个函数，它主要是起到更新的作用 主要就是更新的作用

        endTime = time.time()
        cost = (endTime - startTime) * 1000

        print(u'合约 1小时%s数据下载完成%s，耗时%s毫秒' % (symbol, l[0]['Time'], cost))

        #================================================================================================================              下面是写入df 可以忽略
        df = json2dataframe(result)
        if df.shape[0] == 0:  # 获得df的列数，如果等于0，说明没有返回结果
            print('empty dataframe')
            return df
        last_dt = datetime.datetime.strptime(df['Time'].values[-1], "%Y-%m-%dT%H:%M:%S.%fZ")                                              # 最后一条数据的时间
        while (last_dt - datetime.datetime.strptime(params['end'],
                                           "%Y-%m-%d %H:%M:%S")).total_seconds() < 0:                                   # 如果最后条bar 的时间小于最终需要的时间
            print('loading data to ', last_dt, '... ...',
                  (last_dt - datetime.datetime.strptime(params['end'], "%Y-%m-%d %H:%M:%S")).total_seconds())
            params['start'] = last_dt                                                                                     # 将最后一条bar 的时间设置成为开始时间
            url = "https://api.matrixdata.io/matrixdata/api/v1/barchart" + parse_params_to_str(params)
            result = self.request_get(url)
            resp_ = result['Result']                                                                                                           #避免数据重复
            l = resp_
            for d in l:
                bar = generateVtBar(symbol, d)                                                                                      # 将每条数据灌进去生成bar
                d = bar.__dict__
                flt = {'datetime': bar.datetime}
                cl.replace_one(flt, d, True)                                                     # replaceOne()这个函数，它主要是起到更新的作用 主要就是更新的作用

            endTime = time.time()
            cost = (endTime - startTime) * 1000

            print(u'合约%s数据下载完成%s，耗时%s毫秒' % (symbol, l[0]['Time'], cost))

            temp_df = json2dataframe(result)[1:]
            if temp_df.shape[0] == 0:
                return df
            df = pd.concat((df, temp_df))                                                                                                   # 进行了列的拼接
            last_dt = datetime.datetime.strptime(df['Time'].values[-1], "%Y-%m-%dT%H:%M:%S.%fZ")
        df = df.reset_index()                                                                                                                 # 进行重置索引
        print(df)
        return df


    # ----------------------------------------------------------------------
    def downloadAllMinuteBar(self,start, end):
        """
        params = {'symbol': symbol,
                  'interval': '1h',
                  'start': '2013-04-01 10:01:00',
                  'end': '2018-05-31 00:00:00',
                  }



        :param start:
        :param end:
        :return:
        """
        """下载所有配置中的合约的分钟线数据"""
        print('-' * 50)
        print(u'开始下载合约小时级别线数据')
        print('-' * 50)

        params = {}
        for symbol in SYMBOLS:
            params['start'] = start
            params['end'] = end
            params['symbol'] = symbol
            params['interval'] = '1h'                                                        #注意这里是小时级别的数据
            self.downMinuteBarBySymbol(params)
            time.sleep(1)

        print('-' * 50)
        print(u'合约小时级别线数据下载完成')
        print('-' * 50)





if __name__ == '__main__':
    matrixdata = matrixdata_sdk( debug=True)

    #============================================================================================加载单品类数据

    for symbol in ["DSH/USD.BF"]:
        """
        symbol       交易对
        interval     k线周期（1m,5m,15m,30m,1h,1d）
        start        开始时间(UTC国际时间)，非必填参数，传入时必须同时传入“结束时间”
        end          结束时间(UTC国际时间)，非必填参数，传入时必须同时传入“开始时间”
        limit        限制量(Default 500; max 500)，不传开始结束时间，表示距当前最近的N条数据
        """
        params = {'symbol': symbol,
                  'interval': '1h',
                  'start': '2013-05-01 00:00:00',
                  'end': '2019-05-20 00:00:00',
                  }
        df_bar = matrixdata.downMinuteBarBySymbol(params)
        # print(df_bar)


    # # ----------------------------------------------------------------------
    #
    # print('-' * 550)
    # print(u'开始下载小时级别数据线适合海龟交易策略')
    # print('-' * 550)
    #
    # for symbol in SYMBOLS:
    #     """
    #     symbol       交易对
    #     interval     k线周期（1m,5m,15m,30m,1h,1d）
    #     start        开始时间(UTC国际时间)，非必填参数，传入时必须同时传入“结束时间”
    #     end          结束时间(UTC国际时间)，非必填参数，传入时必须同时传入“开始时间”
    #     limit        限制量(Default 500; max 500)，不传开始结束时间，表示距当前最近的N条数据
    #     """
    #     params = {'symbol': symbol,
    #               'interval': '1h',
    #               'start': '2016-04-01 00:00:00',
    #               'end': '2016-10-15 00:00:00',
    #               }
    #     matrixdata.downMinuteBarBySymbol(params)
    #     time.sleep(1)
    #
    # print('-' * 550)
    # print(u'小时级别trtule数据下载完成')
    # print('-' * 550)


