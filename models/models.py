# -*- coding: utf-8 -*-
from odoo import models, api, fields, _
from odoo.exceptions import ValidationError
import logging
import json
from datetime import datetime
_logger = logging.getLogger(__name__)


class AccountPaymentGroupInherit(models.Model):
    _inherit = "account.payment.group"

    def percentage_amount(self,type_iva,amount_ret_iva,amount_taxed_total_invs):
        if type_iva == '1':
            return amount_ret_iva
        elif type_iva in ['2','4','5']:
            return amount_taxed_total_invs

    def compute_withholdings(self):
        res = super(AccountPaymentGroupInherit, self).compute_withholdings()
        for rec in self:
            # Verificamos por Retenciones de IVA en el proveedor
            type_iva = rec.partner_id.condicion_ret_iva
            if type_iva not in ['3','0']:
                
                amount_taxed_total_invs = 0
                amount_ret_iva = 0
                #Recorremos facturas en pago
                for invs in self.debt_move_line_ids:
                    if invs.move_id.subject_to_withholding:
                        
                        iva10 = False
                        for line in invs.move_id.line_ids:
                                # Verificar si el impuesto es IVA 21%
                                if line.tax_group_id.l10n_ar_vat_afip_code == '5':
                                    amount_taxed_total_invs +=  line.amount_currency
                                    amount_ret_iva += (line.amount_currency  * 0.5)
                                    _logger.warning('amount_ret_iva: {0}'.format(amount_ret_iva))
                                # Verificar si el impuesto es IVA 10.5%
                                elif line.tax_group_id.l10n_ar_vat_afip_code == '4':
                                    iva10 = True
                                    _logger.warning('Hola')
                                    # Si es servicio, se calcula el 80% del impuesto
                                    #if line.product_id.type == 'service':
                                        #amount_taxed_total_invs += line.price_subtotal * 0.105
                                        #amount_ret_iva += (line.price_subtotal * 0.105) * 0.8
                                        #_logger.warning('amount_ret_iva: {0}'.format(amount_ret_iva))
                        #Si se verifica que tenemos iva de 10,5 recorremos las lineas de facturas para detectar si la linea corresponde a un servicio o no
                        #de ser asi sumamos el %80 del impuesto a la retencion
                        if iva10:
                            for line in invs.move_id.invoice_line_ids:
                                        for tax in line.tax_ids:
                                            if tax.tax_group_id.l10n_ar_vat_afip_code == '4':
                                                _logger.warning('Hola 22')
                                                if line.product_id.type == 'service':
                                                    #TODO con este calculo se entiende que el IVA 10,5 se calcula con base en precio de venta pero habria que cambiar para cuando el impuesto esta incluido en el precio
                                                    amount_taxed_total_invs += line.price_unit * 0.105
                                                    amount_ret_iva += (line.price_unit * 0.105) * 0.8
                                                    _logger.warning('amount_ret_iva 10.5: {0}'.format(amount_ret_iva))

                if amount_ret_iva > 0:
                    amount_ret_iva = self.percentage_amount(type_iva,amount_ret_iva,amount_taxed_total_invs)
                _payment_method = self.env.ref(
                    'l10n_ar_withholding.'
                    'account_payment_method_out_withholding')
                _journal = self.env['account.journal'].search([
                    ('company_id', '=', rec.company_id.id),
                    ('outbound_payment_method_line_ids.payment_method_id', '=', _payment_method.id),
                    ('type', 'in', ['cash', 'bank']),
                ], limit=1)
                _imp_ret = self.env['account.tax'].search([
                    ('type_tax_use', '=', rec.partner_type),
                    ('company_id', '=', rec.company_id.id),
                    ('withholding_type', '=', 'partner_iibb_padron'),
                    ('tax_iva_ret','=',True)], limit=1)

                #Busco si el pago ya existe en el payment.group de existir lo elimino y vuelvo a crear
                payment_withholding = self.env[
                'account.payment'].search([
                    ('payment_group_id', '=', rec.id),
                    ('tax_withholding_id', '=', _imp_ret.id),
                ], limit=1)


                if payment_withholding:
                    payment_withholding.unlink()
                #Si no existe impuesto de retencion para la compañia no creamos el pago de retencion
                if len(_imp_ret) == 0:
                    return res
                if amount_ret_iva > 400:
                    rec.payment_ids = [(0,0, {
                        'name': '/',
                        'partner_id': rec.partner_id.id,
                        'payment_type': 'outbound',
                        'journal_id': _journal.id,
                        'tax_withholding_id': _imp_ret.id,
                        'payment_method_description': 'Retencion IVA',
                        'payment_method_id': _payment_method.id,
                        'date': rec.payment_date,
                        'destination_account_id': rec.partner_id.property_account_payable_id.id,
                        'amount': amount_ret_iva,
                        'withholding_base_amount': amount_taxed_total_invs
                    })]

                    # Busco en las lineas de pago cual es el pago de retencion para luego cambiarle en su asiento contable la cuenta, 
                    # esto lo hacemos porque por defecto toma la cuenta del diario y queremos que tome la cuenta configurada en el impuesto
                    line_ret = rec.payment_ids.filtered(lambda r: r.tax_withholding_id.id == _imp_ret.id)
                    line_tax_account = line_ret.move_id.line_ids.filtered(lambda r: r.credit > 0)
                    account_imp_ret = _imp_ret.invoice_repartition_line_ids.filtered(lambda r: len(r.account_id) > 0)
                    if len(account_imp_ret) > 0:
                        #Guardo "Cuenta de efectivo" que tiene el diario
                        cuenta_anterior = line_ret.move_id.journal_id.default_account_id
                        #La cambio por la cuenta que tiene el impuesto de retencion configurada
                        line_ret.move_id.journal_id.default_account_id = account_imp_ret.account_id
                        #Cambio en el Apunte contable del Asiento contable la cuenta que esta configurada en el impuesto de retencion
                        line_tax_account.account_id = account_imp_ret.account_id
                        #Vuelvo a poner en el diario la cuenta que tenia anteriormente
                        line_ret.move_id.journal_id.default_account_id = cuenta_anterior
                        #TODO Este cambio se hace para evitar el error de validacion que hace por defecto en
                        #https://github.com/odoo/odoo/blob/14.0/addons/account/models/account_payment.py#L699
                        #Es necesario revisar si este funcionamiento es correcto o existe una forma diferente de realizar

        return res
