# encoding: UTF-8
"""
立即下载数据到数据库中，用于手动执行更新操作。

bitfinex 交易所的数据是从2013年4月开始的
现在有个问题就是如果数据的下载的时间顺序上是不一致的情况下，是不是后边下载的数据是插入到后边的有没有进行相关的排序，
手动下载之前需要提前查看数据库的文件到的时间节点
"""

from dataService import *

if __name__ == '__main__':
    matrixdata = matrixdata_sdk( debug=True)

    #============================================================================================加载单品类数据

    for symbol in ["ETH/USD.BF"]:
        """
        symbol       交易对
        interval     k线周期（1m,5m,15m,30m,1h,1d）
        start        开始时间(UTC国际时间)，非必填参数，传入时必须同时传入“结束时间”
        end          结束时间(UTC国际时间)，非必填参数，传入时必须同时传入“开始时间”
        limit        限制量(Default 500; max 500)，不传开始结束时间，表示距当前最近的N条数据
        """
        params = {'symbol': symbol,
                  'interval': '1m',
                  'start': '2019-05-28 00:01:00',
                  'end': '2019-10-02 00:00:00',
                  }
        df_bar = matrixdata.downMinuteBarBySymbol(params)
        # 用来检验数据的有效性，打印出来
        print(df_bar)

    # # ----------------------------------------------------------------------
    #
    # print('-' * 550)
    # print(u'开始下载合约分钟线数据')
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
    #               'interval': '1m',
    #               'start': '2017-10-01 10:01:00',
    #               'end': '2018-01-31 00:00:00',
    #               }
    #     matrixdata.downMinuteBarBySymbol(params)
    #     time.sleep(1)
    #
    # print('-' * 550)
    # print(u'合约分钟线数据下载完成')
    # print('-' * 550)






    # #======================================================================================================测试K线数据信息
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
    #               'interval': '1m',
    #               'start': '2017-10-01 10:01:00',
    #               'end': '2018-01-31 00:00:00',
    #               }
    #     df_bar = matrixdata.downMinuteBarBySymbol(params)
