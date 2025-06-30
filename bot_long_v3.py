# Стратегия:
# + Ищем силу тренда
# + Вход на перегибе ма_5м
# + Перед входом проверяем направление мa_30м


from flask import Flask, render_template, request, jsonify
from binance.um_futures import UMFutures
import pandas as pd
import numpy as np
import time
from datetime import datetime
from pytz import timezone
import requests
import keys
import logging
import json, os, signal

app = Flask(__name__)

BOT_NAME = '📈_V3'
SYMBOL = 'ARCUSDT'
WINDOW_MA = 50 # окно для рассчета MA 5 мин
WINDOW_MA_30 = 50 # окно для рассчета MA 30 мин
TREND_THRESHOLD = 0.9990 # порог силы тренда
TREND_THRESHOLD_30m = 1 # порог силы тренда 30м перед точкой входа
PAUSE_BETWEEN_GET_DATA_IN_POSITION = 10 # период между обновлением данных в секундах
TP_1_PROC = 2.5 #  это проценты
TP_2_PROC = 2.5 
TP_3_PROC = 2.5
TP_4_PROC = 2.5
STOP_LOSS_PROC = 0.2   #  это проценты
POS_SIZE = 2000.0 # размер позиции в менетах
ZONE = timezone('Europe/Moscow') # часовой пояс
Bot = True

def start_logging(Bot_name):
    """
    Bot_name - имя бота - str
    """
    logger = logging.getLogger(Bot_name)
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler('bot.log')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)
    return logger

logger = start_logging(BOT_NAME) # старт логирования


def connecting_to_market():
    try:
        client_alex = UMFutures(keys.api_key, keys.secret_key)
        result_alex = f'Подключение client_alex к бирже выполено.'
    except:
        result_alex = f'client_alex: ошибка при подключении к бирже.' 

    try:
        client_den = UMFutures(keys.api_key_den, keys.secret_key_den)
        result_den = f'Подключение client_den к бирже выполено.'
    except:
        result_den = f'client_den: ошибка при подключении к бирже.'

    return client_alex, result_alex, client_den, result_den

clients = connecting_to_market()
client, client_den = clients[0], clients[2]


def send_tg_message(text):
    requests.post(
    'https://api.telegram.org/' +
    'bot{}/sendMessage'.format(keys.TOKEN_tlg_bot), 
    params=dict(chat_id=keys.chat_id_tlg, text=text)
    )
    requests.post(
    'https://api.telegram.org/' +
    'bot{}/sendMessage'.format(keys.TOKEN_tlg_bot_den), 
    params=dict(chat_id=keys.chat_id_tlg_den, text=text)
    )

def get_klines(symbol, interval, lookback):
    max_retries = 3  # лимит попыток
    for _ in range(max_retries):
        try:
            klines = pd.DataFrame(client_den.klines(symbol, interval,  limit = lookback))
            klines.columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'Kline_close_time', 'Quote_asset_volume',
               'Number_of_trades', 'Taker_buy_base_asset_volume', 'Taker_buy_quote_asset_volume', 'ignore']
            klines = klines.iloc[:, :6]
            klines['open_time'] = pd.to_datetime(klines['open_time'], unit='ms')
            float_columns = ['open', 'high', 'low', 'close', 'volume']
            klines[float_columns] = klines[float_columns].astype('float')
            klines['sma'] = klines['close'].rolling(window = WINDOW_MA).mean()
            klines['trend'] =  np.where((klines['sma'].iloc[-1] / klines['sma'].iloc[-2]) >= 1, 1, 0)
            klines['trend_power'] =  klines['sma']/klines['sma'].shift()
            return klines
        except Exception as e:
            logger.error(f"Ошибка получения свечей: {str(e)}, время: {get_time()}")
            time.sleep(60)
    raise ConnectionError("Не удалось получить данные после 3 попыток")

def get_time():
    t = datetime.now(ZONE).strftime('%Y-%m-%d__%H-%M-%S')
    return t

def start_next_5m_kline():
    num_sec = 310 - (time.localtime().tm_min % 5) * 60 - time.localtime().tm_sec
    return num_sec

def send_limit_order_buy(simbol_, price_, pos_size_):
    '''функция отправляет лимитный ордер с мгновенным исполнением
       price_ - текущая цена
       POS_SIZE: float, объем позиции в монетах, целое число
    '''
    order_alex = client.new_order(symbol = simbol_, side = 'BUY', type = 'LIMIT', price = round((price_ * 1.01), 4), quantity = pos_size_, timeInForce = 'GTC')
    order_den = client_den.new_order(symbol = simbol_, side = 'BUY', type = 'LIMIT', price = round((price_ * 1.01), 4), quantity = pos_size_, timeInForce = 'GTC')


def send_limit_order_sell(simbol_, price_, pos_size_):
    '''функция отправляет лимитный ордер с мгновенным исполнением
       price_ - текущая цена
       POS_SIZE: float, объем позиции в монетах, целое число
    '''
    order_alex = client.new_order(symbol = simbol_, side = 'SELL', type = 'LIMIT', price = round((price_ / 1.01), 4), quantity = pos_size_, timeInForce = 'GTC')
    order_den = client_den.new_order(symbol = simbol_, side = 'SELL', type = 'LIMIT', price = round((price_ / 1.01), 4), quantity = pos_size_, timeInForce = 'GTC')
   

def TAKE_PROFIT_CALC_LONG(TAKE_PROFIT_PROC, price):
    take_profit = round(price * (1 + TAKE_PROFIT_PROC/100), 5)
    return take_profit

def STOP_LOSS_CALC_LONG(LOSS_PERC, price):
    stop_loss = round(price / (1 + (LOSS_PERC/100)), 5)
    return stop_loss   

def bot(search_trend_power=False, open_position=False, search_pattern = False, open_position_3_4 = False, open_position_1_2 = False, open_position_1_4 =False):
    
    logger.info(f'Бот {BOT_NAME} запущен, время: {get_time()}')
    send_tg_message(f'{BOT_NAME} Бот запущен🚀🚀🚀, время: {get_time()}')
    logger.info(f'Торгует монетой: {SYMBOL}')
    send_tg_message(f'{BOT_NAME} Торгует монетой: {SYMBOL}')
    logger.info(f'Объем позиции, монет: {POS_SIZE}')
    send_tg_message(f'{BOT_NAME} Объем позиции, монет: {POS_SIZE}')
    logger.info(connecting_to_market()[1])
    send_tg_message(f'{BOT_NAME} {connecting_to_market()[1]}')
    logger.info(connecting_to_market()[3])
    send_tg_message(f'{BOT_NAME} {connecting_to_market()[3]}')
    
    while Bot == True:
        qty_current = POS_SIZE # обновляем значение объема
        search_trend_power = True  # включение поиска силы тренда
      
        while search_trend_power == True:   

            df_5m = get_klines(symbol = SYMBOL, interval = '5m', lookback = 52)  
            ##################################################################################################################### 
            trend_power = round(df_5m['trend_power'].iloc[-1], 4)
            if  df_5m['trend_power'].iloc[-1] < TREND_THRESHOLD:  # если сила тренда меньше порога

                logger.info(f'Сила тренда найдена, trend_power =  {trend_power}, время: {get_time()}')
                send_tg_message(f'{BOT_NAME} Сила тренда найдена, trend_power =  {trend_power}, время: {get_time()}')
                search_trend_power == False # отключение поиска силы тренда
                search_pattern = True # включение поиска паттерна ПЕРЕГИБ НА РОСТ 5_М
                time.sleep(start_next_5m_kline()) # пауза
                logger.info(f'Поиск точки входа..., время: {get_time()}')
                send_tg_message(f'{BOT_NAME} Поиск точки входа..., время: {get_time()}')

                
                while search_pattern == True:   

                    df_5m = get_klines(symbol = SYMBOL, interval = '5m', lookback = 52)  
                    ########################################################################################################
                    # если паттерн нашли 
                    if (df_5m['trend'].iloc[-1] == 1) and (df_5m['close'].iloc[-1] > df_5m['sma'].iloc[-1]):
                        time.sleep(4)
                        ####################################################################################
                        # проверяем направление ma_30m
                        df_30m = get_klines(symbol = SYMBOL, interval = '30m', lookback = 52)

                        if  df_30m['trend_power'].iloc[-1] >= TREND_THRESHOLD_30m:

                            search_pattern = False # отключение поиска паттерна 
                            open_position = True # включение позиция открыта
                            date_entry = get_time() # получаем дату входа
                            price_entry = df_5m['close'].iloc[-1] # получаем цену входа
                            price_sma = df_5m['sma'].iloc[-1] # цена sma
                            qty_current = POS_SIZE
                            TP_1 = TAKE_PROFIT_CALC_LONG(TP_1_PROC, price_entry)  # рассчитываем тейки  
                            TP_2 = TAKE_PROFIT_CALC_LONG(TP_2_PROC, price_entry)    
                            TP_3 = TAKE_PROFIT_CALC_LONG(TP_3_PROC, price_entry)   
                            TP_4 = TAKE_PROFIT_CALC_LONG(TP_4_PROC, price_entry) 
                            stop_loss = STOP_LOSS_CALC_LONG(STOP_LOSS_PROC, price_sma)
                            # открываем позицию
                            send_limit_order_buy(SYMBOL, price_entry, POS_SIZE)
                            logger.info(f'Открыта позиция в ЛОНГ⬆️⬆️⬆️ по цене {price_entry}, время: {date_entry}')
                            send_tg_message(f'{BOT_NAME} Открыта позиция в ЛОНГ⬆️⬆️⬆️ по цене {price_entry}, время: {date_entry}')
                            time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION) # пауза

                            while open_position == True:
                                
                                df_5m = get_klines(symbol = SYMBOL, interval = '5m', lookback = 52)
                                ########################################################################################################
                                # если цена дошла до TP_1
                                if df_5m['close'].iloc[-1] > TP_1: # если цена дошла до TP_1
                                    open_position = False
                                    open_position_3_4 = True
                                    price_tp_1 = df_5m['close'].iloc[-1]
                                    date_tp_1 = get_time() # получаем дату TP_1
                                    # закрываем TP_1
                                    send_limit_order_sell(SYMBOL, price_tp_1, POS_SIZE/4)
                                    qty_current -= POS_SIZE/ 4 # обновляем значение действующего объема
                                    logger.info(f'Закрыта позиция по TP_1✅, время: {date_tp_1}, цена: {price_tp_1}')
                                    send_tg_message(f'{BOT_NAME} Закрыта позиция по TP_1✅, время: {date_tp_1}, цена: {price_tp_1}')
                                    time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION) # пауза

                                    while open_position_3_4 == True:
                                        
                                        df_5m = get_klines(symbol = SYMBOL, interval = '5m', lookback = 52)
                                        ########################################################################################################
                                        # если цена дошла до TP_2
                                        if df_5m['close'].iloc[-1] > TP_2: # если цена дошла до TP_2 
                                            open_position_3_4 = False
                                            open_position_1_2 = True
                                            price_tp_2 = df_5m['close'].iloc[-1]
                                            date_tp_2 = get_time() # получаем дату TP_2
                                            # закрываем TP_2
                                            send_limit_order_sell(SYMBOL, price_tp_2, POS_SIZE/4)
                                            qty_current -= POS_SIZE/ 4 # обновляем значение действующего объема
                                            logger.info(f'Закрыта позиция по TP_2✅✅, время: {date_tp_2}, цена: {price_tp_2}')
                                            send_tg_message(f'{BOT_NAME} Закрыта позиция по TP_2✅✅, время: {date_tp_2}, цена: {price_tp_2}')
                                            time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION) # пауза
                                            
                                            while open_position_1_2 == True:

                                                df_5m = get_klines(symbol = SYMBOL, interval = '5m', lookback = 52)                                            
                                                ########################################################################################################
                                                # если цена дошла до TP_3
                                                if df_5m['close'].iloc[-1] > TP_3: # если цена дошла до TP_3 
                                                    open_position_1_2 = False
                                                    open_position_1_4 = True
                                                    price_tp_3 = df_5m['close'].iloc[-1]
                                                    date_tp_3 = get_time() # получаем дату TP_3
                                                    # закрываем TP_3
                                                    send_limit_order_sell(SYMBOL, price_tp_3, POS_SIZE/4)
                                                    qty_current -= POS_SIZE/ 4 # обновляем значение действующего объема
                                                    logger.info(f'Закрыта позиция по TP_3✅✅✅, время: {date_tp_3}, цена: {price_tp_3}')
                                                    send_tg_message(f'{BOT_NAME} Закрыта позиция по TP_3✅✅✅, время: {date_tp_3}, цена: {price_tp_3}')
                                                    time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION) # пауза
                                        
                                                    while open_position_1_4 == True:

                                                        df_5m = get_klines(symbol = SYMBOL, interval = '5m', lookback = 52) 
                                                        #######################################################################################################

                                                        if df_5m['close'].iloc[-1] > TP_4:

                                                            open_position_1_4 = False
                                                            price_tp_4 = df_5m['close'].iloc[-1] 
                                                            date_tp_4 = get_time() # получаем дату TP_4
                                                            # закрываем TP_4
                                                            send_limit_order_sell(SYMBOL, price_tp_4, POS_SIZE/4)
                                                            logger.info(f'Закрыта позиция по TP_4✅✅✅✅, время: {date_tp_4}, цена: {price_tp_4}')
                                                            send_tg_message(f'{BOT_NAME} Закрыта позиция по TP_4✅✅✅✅, время: {date_tp_4}, цена: {price_tp_4}')
                                                            time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION) # пауза
                                                            break
                                                        
                                                        
                                                        
                                                        elif df_5m['close'].iloc[-1] < df_5m['sma'].iloc[-1]:

                                                            open_position_1_4 = False
                                                            price_tp_4 = df_5m['close'].iloc[-1] 
                                                            date_tp_4 = get_time() # получаем дату TP_4
                                                            # закрываем sma
                                                            send_limit_order_sell(SYMBOL, price_tp_4, POS_SIZE/4)
                                                            logger.info(f'Закрыта позиция по sma(TP_4)✅, время: {date_tp_4}, цена: {price_tp_4}')
                                                            send_tg_message(f'{BOT_NAME} Закрыта позиция по sma(TP_4)✅, время: {date_tp_4}, цена: {price_tp_4}')
                                                            time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION) # пауза
                                                            break
                                                

                                                        elif df_5m['close'].iloc[-1] < price_entry: # стоп-лосс
                                                            open_position_1_4 = False
                                                            price_stop = df_5m['close'].iloc[-1]
                                                            date_stop = get_time() # получаем дату стопа
                                                            # закрываем позицию 
                                                            send_limit_order_sell(SYMBOL, price_stop, POS_SIZE/4)
                                                            logger.info(f'Закрыта позиция по Б/У🟥, время: {date_stop}')
                                                            send_tg_message(f'{BOT_NAME} Закрыта позиция по Б/У🟥, время: {date_stop}')
                                                            time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION) # пауза
                                                            break
                                            

                                                        else:  
                                                            # ищем TP_4
                                                            logger.info(f'Поиск TP_4, время: {get_time()}')
                                                            time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION)


                                                elif df_5m['close'].iloc[-1] < price_entry: # стоп-лосс
                                                    open_position_1_2 = False
                                                    price_stop = df_5m['close'].iloc[-1]
                                                    date_stop = get_time() # получаем дату стопа
                                                    # закрываем позицию 
                                                    send_limit_order_sell(SYMBOL, price_stop, qty_current)
                                                    logger.info(f'Закрыта позиция по Б/У(TP_3)🟥, цена: {price_stop}, время: {date_stop}')
                                                    send_tg_message(f'{BOT_NAME} Закрыта позиция по Б/У(TP_3)🟥, цена: {price_stop}, время: {date_stop}')
                                                    time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION) # пауза
                                                    break            


                                                elif df_5m['close'].iloc[-1] < df_5m['sma'].iloc[-1]: # стоп-лосс
                                                    open_position_1_2 = False
                                                    price_stop = df_5m['close'].iloc[-1]
                                                    date_stop = get_time() # получаем дату стопа
                                                    # закрываем позицию 
                                                    send_limit_order_sell(SYMBOL, price_stop, qty_current)
                                                    logger.info(f'Закрыта позиция по sma(TP_3)✅, цена: {price_stop}, время: {date_stop}')
                                                    send_tg_message(f'{BOT_NAME} Закрыта позиция по sma(TP_3)✅, цена: {price_stop}, время: {date_stop}')
                                                    time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION) # пауза
                                                    break
                                    

                                                else:  
                                                    # ищем TP_3
                                                    logger.info(f'Поиск TP_3, время: {get_time()}')
                                                    time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION)

                        
                                        elif df_5m['close'].iloc[-1] < price_entry: # стоп-лосс
                                            open_position_3_4 = False
                                            price_stop = df_5m['close'].iloc[-1]
                                            date_stop = get_time() # получаем дату стопа
                                            # закрываем позицию 
                                            send_limit_order_sell(SYMBOL, price_stop, qty_current)
                                            logger.info(f'Закрыта позиция по Б/У(TP_2)🟥, цена: {price_stop}, время: {date_stop}')
                                            send_tg_message(f'{BOT_NAME} Закрыта позиция по Б/У(TP_2)🟥, цена: {price_stop}, время: {date_stop}')
                                            time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION) # пауза
                                            break
                        

                                        else:  
                                            # ищем TP_2
                                            logger.info(f'Поиск TP_2, время: {get_time()}')
                                            time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION)    



                                elif df_5m['close'].iloc[-1] < stop_loss: # если ниже стопа
                                    
                                    open_position = False
                                    price_stop = df_5m['close'].iloc[-1]
                                    date_stop = get_time() # получаем дату стопа
                                    # закрываем позицию 
                                    send_limit_order_sell(SYMBOL, price_stop, qty_current)
                                    logger.info(f'Закрыта позиция по СТОПУ❌, цена: {price_stop}, время: {date_stop}')
                                    send_tg_message(f'{BOT_NAME} Закрыта позиция по СТОПУ❌, цена: {price_stop}, время: {date_stop}')
                                    time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION) # пауза
                                    break                                                        
                                                                                                                                                                                            

                                                                                                    
                                else:  
                                    # ищем TP_1
                                    logger.info(f'Поиск TP_1, время: {get_time()}')
                                    time.sleep(PAUSE_BETWEEN_GET_DATA_IN_POSITION)

                        else:  
                            # завершаем поиск, так как ма_30м - в контртренде
                            logger.info(f'Отмена поиска точки входа - контр-тренд, время: {get_time()}')
                            send_tg_message(f'{BOT_NAME} Отмена поиска точки входа - контр-тренд, время: {get_time()}')
                            time.sleep(60)  
                            break          
                                                                                                                    

                    else:  
                        # ищем паттерн - точка входа
                        logger.info(f'Поиск точки входа, время: {get_time()}')
                        time.sleep(60)

            else:
                logger.info(f'Сила тренда: {trend_power}, время: {get_time()}')
                time.sleep(start_next_5m_kline())
                # ищем силу тренда падающего start_next_5m_kline()  


@app.route('/')
def index():
    mess_1 = "The app LONG_V3 is ready!"
    params = {'SYMBOL:' : SYMBOL,
              'TREND_THRESHOLD' : TREND_THRESHOLD,
              'PAUSE_BETWEEN_GET_DATA_IN_POSITION' : PAUSE_BETWEEN_GET_DATA_IN_POSITION,
              'TP_1_PROC' : TP_1_PROC,
              'TP_2_PROC' : TP_2_PROC,
              'TP_3_PROC' : TP_3_PROC, 
              'TP_4_PROC' : TP_4_PROC, 
              'STOP_LOSS_PROC' : STOP_LOSS_PROC,
              'POS_SIZE' : POS_SIZE}
    
    return jsonify(mess_1, params)

@app.route('/server_stop', methods=['GET'])
def stopServer():
    os.kill(os.getpid(), signal.SIGINT)
    return jsonify({ "success": True, "message": "Server is shutting down..." })

@app.route('/change_value?TP_1_PROC', methods=['GET', 'POST'])
def change_value():
    TP_1_PROC = request.args.get('TP_1_PROC')
    return logger.info(f'Новое значение: {TP_1_PROC}')


@app.route('/bot_run')
def bot_run():

    try:
        bot(search_trend_power=False, open_position=False, search_pattern = False, open_position_3_4 = False, open_position_1_2 = False, open_position_1_4 =False)
    except:
        logger.info(f'Бот остановлен⚠️⚠️⚠️⚠️⚠️⚠️, время: {get_time()}') 
        send_tg_message(f'{BOT_NAME} Бот остановлен⚠️⚠️⚠️⚠️⚠️⚠️, время: {get_time()}')   


if __name__ == '__main__':
    app.run(host='0.0.0.0', port = 100, debug=True)                