from app.services.insurance_gps import apply_insurance_gps

class P:
    def __init__(self,n,text,method='digital'):
        self.page_number=n; self.text=text; self.extraction_method=method
class D:
    def __init__(self,text,pages=None):
        self.pages=pages or [P(1,text)]; self.full_text='\n'.join(p.text for p in self.pages); self.filename='sample.pdf'; self.used_ocr=False

def vals(fields): return {x['name']:x['value'] for x in fields}

def test_sinoasia_real_title_fields():
    text='''ДОГОВОР СТРАХОВАНИЯ № 5544360-BCCL\nСтраховщик АО СК «Sinoasia B&R (СиноАзия БиЭндАр)»\nСтрахователь Товарищество с ограниченной ответственностью "АгроТехМенеджмент"\nДоговор залога/займа №OPA/2026/U/S/037562\nСтраховая сумма 37 508 400.00\nСтраховая премия 900 201.00\nСтраховой тариф 2,4%\nС 02.07.2026 по 01.07.2027 гг.\nДата и место заключения г. Алматы, 01.07.2026 г.'''
    f,t=apply_insurance_gps(D(text),'insurance_contract',[],[]); v=vals(f)
    assert v['insurance_policy_number']=='5544360-BCCL'
    assert v['insurance_sum_kzt']==37508400.0
    assert v['insurance_premium_kzt']==900201.0
    assert v['insurance_start_date']=='02.07.2026'
    assert v['insurance_end_date']=='01.07.2027'

def test_pilot_gps_appendix_totals():
    p1=P(1,'ДОГОВОР № Pilot/ESPUlOV/090726\nг. Алматы «09» июля 2026г.\nИП «Pilot-company», именуемое Поставщик, и ИП «ЕСПУЛОВ», именуемое Заказчик')
    p4=P(4,'GPS-трекер 56 000 1 56 000\nАбонентская плата за доступ к системе GPS мониторинга 2 500 1 2 500\nИТОГО, тенге с НДС, за 1 год 30 000 1 30 000')
    f,t=apply_insurance_gps(D('',[p1,p4]),'gps_service_contract',[],[]); v=vals(f)
    assert v['gps_contract_number']=='PILOT/ESPULOV/090726'
    assert v['gps_device_quantity']==1
    assert v['gps_equipment_total_kzt']==56000.0
    assert v['gps_annual_fee_kzt']==30000.0
    assert v['gps_service_fee_kzt']==86000.0

def test_insurance_payment_order():
    text='''Платежное поручение № 3654 от 03.07.2026\nСумма прописью: 900 201.00 Девятьсот тысяч\nстраховая премия по счету на оплату № 5544360 от 01.07.2026г.'''
    f,t=apply_insurance_gps(D(text),'unknown',[],[]); v=vals(f)
    assert v['insurance_payment_order_number']=='3654'
    assert v['insurance_payment_amount_kzt']==900201.0
    assert any(x['name']=='insurance_gps_payment_rows' for x in t)
