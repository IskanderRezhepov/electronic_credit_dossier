from app.services.document_reader import PageContent, ReadDocument
from app.services.layout_tables import assets_from_layout, schedule_from_layout


def word(text,x,y,w=55,h=10):
    return {'text':text,'x0':x,'y0':y,'x1':x+w,'y1':y+h,'confidence':1.0}


def test_coordinate_schedule_columns():
    words=[]
    for text,x in [('Дата',10),('Основной долг',120),('Вознаграждение',260),('Платеж',390),('Остаток',510)]: words.append(word(text,x,10,90))
    for y,date,principal,interest,payment,balance in [(40,'05.01.2026','100 000,00','10 000,00','110 000,00','900 000,00'),(60,'05.02.2026','100 000,00','9 000,00','109 000,00','800 000,00')]:
        for text,x,w in [(date,10,80),(principal,120,90),(interest,260,80),(payment,390,90),(balance,510,90)]: words.append(word(text,x,y,w))
    page=PageContent(1,'ГРАФИК ПЛАТЕЖЕЙ','digital',17,0.99,layout_words=words,page_width=650,page_height=800)
    table=schedule_from_layout(ReadDocument('x.pdf',1,'pdf',[page]))
    assert table and table['row_count']==2
    assert table['rows'][0]['principal']==100000.0
    assert table['rows'][0]['balance']==900000.0


def test_coordinate_asset_specification():
    words=[]
    for text,x in [('Наименование',10),('Модель',170),('Количество',270),('VIN',350),('Цена за единицу',500),('Итого',650)]: words.append(word(text,x,10,120))
    for text,x,w in [('Самосвал',10,120),('X3000',170,70),('2',270,20),('LZGJL4V44PX123456',350,130),('45 000 000,00',500,110),('90 000 000,00',650,110)]: words.append(word(text,x,40,w))
    page=PageContent(1,'СПЕЦИФИКАЦИЯ','digital',13,0.99,layout_words=words,page_width=800,page_height=900)
    table=assets_from_layout(ReadDocument('x.pdf',1,'pdf',[page]))
    assert table and table['row_count']==1
    row=table['rows'][0]
    assert row['quantity']==2
    assert row['unit_price_kzt']==45000000.0
    assert row['total_amount_kzt']==90000000.0
