"""
展示如何执行参数优化。
"""
from vnpy.trader.app.ctaStrategy.ctaBacktesting import BacktestingEngine, OptimizationSetting

if __name__ == '__main__':
    from StrategyBollBand import BollBandsStrategy

    # 创建回测引擎
    engine = BacktestingEngine()

    # 设置引擎的回测模式为K线
    engine.setBacktestingMode(engine.BAR_MODE)

    # 设置使用的历史数据库
    engine.setDB_URI("mongodb://localhost:32768")
    engine.setDatabase("VnTrader_1Min_Db")

    # 设置回测用的数据起始日期
    engine.setStartDate('20181214 23:00:00', initHours=120)
    engine.setEndDate('20190314 23:00:00')

    # #设置产品相关参数
    contracts = [
        {"symbol": "ETHUSD:BITFINEX",
         "size": 10,
         "priceTick": 0.001,
         "rate": 5 / 10000,
         "slippage": 0.005
         }]

    engine.setContracts(contracts)  # 设置回测合约相关数据

    # 跑优化
    setting = OptimizationSetting()  # 新建一个优化任务设置对象
    setting.setOptimizeTarget('totalNetPnl')  # 设置优化排序的目标是策略净盈利

    setting.addParameter('fastWindow', 55, 60, 5)  # 增加第一个优化参数atrLength，起始12，结束20，步进2
    setting.addParameter('slowWindow', 70, 75, 5)  # 增加第二个优化参数atrMa，起始20，结束30，步进5
    setting.addParameter('bBandPeriod', 40)        # 增加一个固定数值的参数


    # 性能测试环境：I7-3770，主频3.4G, 8核心，内存16G，Windows 7 专业版
    # 测试时还跑着一堆其他的程序，性能仅供参考
    import time
    start = time.time()

    # # 运行单进程优化函数，自动输出结果，耗时：359秒
    # engine.runOptimization(BollBandsStrategy, setting)

    # 多进程优化，耗时：89秒
    engine.runParallelOptimization(BollBandsStrategy, setting)

    print(u'耗时：%s' % (time.time() - start))