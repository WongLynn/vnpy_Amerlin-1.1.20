#!/usr/bin/env python
# coding=utf-8

"""
该程序主要是从matrixdata 之中获取交易所相关的数据信息
madeby；yalinwang
date:2018-11-12
QQ:894510791
该文件为数据源文件sdk，文件格式为数据源原始文件格式
"""

from __future__ import print_function
import json
import time
import datetime
import requests
# from datetime import datetime,timedelta

import pandas as pd
pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', None)
pd.set_option('expand_frame_repr', False)                                # 当列太多时不换行

from vnpy.trader.vtObject import VtBarData
from vnpy.trader.app.ctaStrategy.ctaBase import MINUTE_DB_NAME           #仅仅引入分钟级别的数据库
from pymongo import MongoClient, ASCENDING



# 加载配置
config = open('config.json')
setting = json.load(config)

MONGO_HOST = setting['MONGO_HOST']
MONGO_PORT = setting['MONGO_PORT']
APIKEY = setting['APIKEY']
SYMBOLS = setting['SYMBOLS']

mc = MongoClient(MONGO_HOST, MONGO_PORT)                                  # Mongo连接
db = mc[MINUTE_DB_NAME]                                                   # 数据库,该数据库下的


def generateVtBar(symbol, d):                                              #传入交易对以及每条记录信息   "BTC/USD.BF"
    """生成K线"""

    l = symbol.split('/')          #[BTC, USD.BF]
    m = l[-1]                      # USD.BF
    n = m.split('.')               # [USD,BF]
    bar = VtBarData()
    bar.symbol = l[0] + n[0]       #BTCUSD
    # bar.exchange = n[-1]
    bar.exchange = 'BITFINEX'
    bar.vtSymbol = ':'.join([bar.symbol, bar.exchange])


    # bar.datetime = datetime.datetime.strptime(d['time_open'], '%Y-%m-%dT%H:%M:%S.%f0Z')     #由字符串格式转化为日期格式的函数为: datetime.datetime.strptime()
    # bar.date = bar.datetime.strftime('%Y%m%d')                #时间转字符串
    # bar.time = bar.datetime.strftime('%H:%M:%S')              #时间转字符串
    # bar.open = d['price_open']                                #对应生成
    # bar.high = d['price_high']                                #对应生成
    # bar.low = d['price_low']                                  #对应生成
    # bar.close = d['price_close']                              #对应生成
    # bar.volume = d['volume_traded']                           #对应生成


    #===============================对数据库文件格式进行处理==================================
    # 目标 'time', 'open', 'high', 'low', 'close', 'volume', 'amount'
    """
     #源数据 d {'Symbol': 'BTC/USD.BF', 
                'Time': '2017-10-04T10:00:00.000Z', 
                'Open': '4248.2', 
                'High': '4250', 
                'Low': '4218.3', 
                'Close': '4245',
                 QuoteVolume': '1512.60001705',
                'QuoteAssetVolume': '0', 
                'TradeNum': '0'
            }
    """
    bar.datetime = datetime.datetime.strptime(d['Time'],'%Y-%m-%dT%H:%M:%S.%f0Z')     # 由字符串格式转化为日期格式的函数为: datetime.datetime.strptime()
    bar.open = d['Open']                                    #对应生成
    bar.high = d['High']                                    #对应生成
    bar.low = d['Low']                                      #对应生成
    bar.close = d['Close']                                  #对应生成
    bar.volume = d['QuoteAssetVolume']                      #对应生成
    bar.amount = d['QuoteVolume']                           #对应生成
    bar.date = bar.datetime.date().strftime('%Y%m%d')       #时间转字符串
    bar.time = bar.datetime.time().strftime('%H:%M')


    return bar

# 拼接url
def parse_params_to_str(params):                                                                      # 将参数转换成字符串
    url = '?'
    for key, value in params.items():
        url = url + str(key) + '=' + str(value) + '&'
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
    def __init__(self,token = "",debug = False):
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

    def get_bar(self, params):
        """
        所有的json 导入的数据格式都是str
        params = {'symbol': symbol,
          'interval': '1h',
          'start': '2017-10-01 10:01:00',
          'end': '2018-05-31 00:00:00',
          }
        :param params:
        :return:
        """
        params['end'] = str(params['end'])
        symbol = params['symbol']
        start = str(params['start'])
        end = str(params['end'])


        startTime = time.time()                                                   # 1557571175.43439
        cl = db[symbol]                                                           # 即定义的数据库1 min 数据库表空间
        cl.ensure_index([('datetime', ASCENDING)], unique=True)                   # cl.ensure_index([("索引字段名", 1)], unique=True)

        # startDt = datetime.datetime.strptime(start, '%Y%m%d')                     # 由字符串格式转化为日期格式的函数为: datetime.datetime.strptime()
        # endDt = datetime.datetime.strptime(end, '%Y%m%d')                         # 由字符串格式转化为日期格式的函数为: datetime.datetime.strptime()


        url = "https://api.matrixdata.io/matrixdata/api/v1/barchart" + parse_params_to_str(params)
        result = self.request_get(url)
        print('result', result)                                                   #resut 返回json 格式
        print('type', type(result))                                               #class dict
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

        type <class 'dict'>
        """

        # ===================== 接下来的一部分是将其写入数据库之中  进行数据的处理将返回的数据截取成数组
        resp = result['Result']
        print("resp", resp)
        """
            [
        {
        'Symbol':'BTC/USD.BF',
        'Time':'2017-10-01T11:00:00.000Z',
        'Open':'4325.9',
        'High':'4330',
        'Low':'4311.1',
        'Close':'4312.1',
        'QuoteVolume':'625.37708273',
        'QuoteAssetVolume':'0',
        'TradeNum':'0'
        },
        {
        'Symbol':'BTC/USD.BF',
        'Time':'2017-10-01T12:00:00.000Z',
        'Open':'4312.1',
        'High':'4312.1',
        'Low':'4269.6',
        'Close':'4285',
        'QuoteVolume':'2160.01277461',
        'QuoteAssetVolume':'0',
        'TradeNum':'0'
        },

        ]

        type resp <class 'list'>
        """
        # l = resp.json()  # 这一步要不转换成json 要不不转化     报错暂时不处理
        # print("l",l)
        l = resp
        for d in l:
            print('d', d)
            """
            d {'Symbol': 'BTC/USD.BF', 'Time': '2017-10-04T01:00:00.000Z', 'Open': '4297.6', 'High': '4311.5', 'Low': '4296.8', 'Close': '4302.9', 'QuoteVolume': '350.28416325', 'QuoteAssetVolume': '0', 'TradeNum': '0'}
            d {'Symbol': 'BTC/USD.BF', 'Time': '2017-10-04T02:00:00.000Z', 'Open': '4302.9', 'High': '4304', 'Low': '4285', 'Close': '4293.8', 'QuoteVolume': '352.1880458', 'QuoteAssetVolume': '0', 'TradeNum': '0'}
            """
            bar = generateVtBar(symbol, d)                                         # 将每条数据灌进去生成bar
            d = bar.__dict__
            flt = {'datetime': bar.datetime}
            cl.replace_one(flt, d, True)                                           # replaceOne()这个函数，它主要是起到更新的作用 主要就是更新的作用

        endTime = time.time()
        cost = (endTime - startTime) * 1000

        print(u'合约%s数据下载完成%s，耗时%s毫秒' % (symbol, l[0]['Time'], cost))

        # #====================================  下面是写入df 可以忽略
        # df = json2dataframe(result)
        # if df.shape[0] == 0:  # 获得df的列数，如果等于0，说明没有返回结果
        #     print('empty dataframe')
        #     return df
        # last_dt = datetime.datetime.strptime(df['Time'].values[-1], "%Y-%m-%dT%H:%M:%S.%fZ")  # 最后一条数据的时间
        # while (last_dt - datetime.datetime.strptime(params['end'],
        #                                    "%Y-%m-%d %H:%M:%S")).total_seconds() < 0:  # 如果最后条bar 的时间小于最终需要的时间
        #     print('loading data to ', last_dt, '... ...',
        #           (last_dt - datetime.datetime.strptime(params['end'], "%Y-%m-%d %H:%M:%S")).total_seconds())
        #     params['start'] = last_dt  # 将最后一条bar 的时间设置成为开始时间
        #     url = "https://api.matrixdata.io/matrixdata/api/v1/barchart" + parse_params_to_str(params)
        #     result = self.request_get(url)
        #     temp_df = json2dataframe(result)[1:]
        #     if temp_df.shape[0] == 0:
        #         return df
        #     df = pd.concat((df, temp_df))  # 进行了列的拼接
        #     last_dt = datetime.datetime.strptime(df['Time'].values[-1], "%Y-%m-%dT%H:%M:%S.%fZ")
        # df = df.reset_index()  # 进行重置索引
        # return df



if __name__ == '__main__':

    token = "MLZONAFF"
    matrixdata = matrixdata_sdk(token =token,debug = True)

    #======================================================================================================测试K线数据信息

    for symbol in ["BTC/USD.BF"]:
        """
        symbol       交易对
        interval     k线周期（1m,5m,15m,30m,1h,1d）
        start        开始时间(UTC国际时间)，非必填参数，传入时必须同时传入“结束时间”
        end          结束时间(UTC国际时间)，非必填参数，传入时必须同时传入“开始时间”
        limit        限制量(Default 500; max 500)，不传开始结束时间，表示距当前最近的N条数据
        """
        params = {'symbol': symbol,
                  'interval': '1m',
                  'start': '2017-10-01 10:01:00',
                  'end': '2018-01-31 00:00:00',
                  }
        df_bar = matrixdata.get_bar(params)

        # df_bar = df_bar[['Symbol', 'Time', 'QuoteAssetVolume', 'QuoteVolume', 'Open', 'Low', 'High', 'Close']]
        print(df_bar)









