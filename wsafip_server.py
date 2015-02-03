# -*- coding: utf-8 -*-
##############################################################################
#
# Copyright (C) 2012 OpenERP - Team de Localización Argentina.
# https://launchpad.net/~openerp-l10n-ar-localization
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
from openerp.osv import fields, osv
from openerp.tools.translate import _
from suds.client import Client
from suds import WebFault
import logging
import sys
from sslhttps import HttpsTransport

# logging.getLogger('suds.transport').setLevel(logging.DEBUG)

_logger = logging.getLogger(__name__)

def _update(pool, cr, uid, model_name, remote_list, can_create=True, domain=[]):
    model_obj = pool.get(model_name) 

    # Build set of AFIP codes
    rem_afip_code_set = set([ i['afip_code'] for i in remote_list ])

    # Take exists instances 
    sto_ids = model_obj.search(cr, uid, [('active','in',['f','t'])] + domain)
    sto_list = model_obj.read(cr, uid, sto_ids, ['afip_code'])
    sto_afip_code_set = set([ i['afip_code'] for i in sto_list ])

    # Append new afip_code
    to_append = rem_afip_code_set - sto_afip_code_set
    if to_append and can_create:
        for item in [ i for i in remote_list if i['afip_code'] in to_append ]:
            model_obj.create(cr, uid, item)
    elif to_append and not can_create:
        _logger.warning('New items of type %s in WS. I will not create them.' % model_name)

    # Update active document types
    to_update = rem_afip_code_set & sto_afip_code_set
    update_dict = dict( [ (i['afip_code'], i['active']) for i in remote_list
                         if i['afip_code'] in to_update ])
    to_active = [ k for k,v in update_dict.items() if v ]
    if to_active:
        model_ids = model_obj.search(cr, uid, [('afip_code','in',to_active),('active','in',['f',False])])
        model_obj.write(cr, uid, model_ids, {'active':True})

    to_deactive = [ k for k,v in update_dict.items() if not v ]
    if to_deactive:
        model_ids = model_obj.search(cr, uid, [('afip_code','in',to_deactive),('active','in',['t',True])])
        model_obj.write(cr, uid, model_ids, {'active':False})

    # To disable exists local afip_code but not in remote
    to_inactive = sto_afip_code_set - rem_afip_code_set 
    if to_inactive:
        model_ids = model_obj.search(cr, uid, [('afip_code','in',list(to_inactive))])
        model_obj.write(cr, uid, model_ids, {'active':False})

    _logger.info('Updated %s items' % model_name)

    return True

class wsafip_server(osv.osv):
    _name = "wsafip.server"
    _inherit = "wsafip.server"

    """
    Ref: https://www.afip.gob.ar/fe/documentos/manual_desarrollador_COMPG_v2.pdf
    TODO:
        AFIP Description: Método de obtención de CAEA (FECAEASolicitar)
        AFIP Description: Método de consulta de CAEA (FECAEAConsultar)
        AFIP Description: Método para informar CAEA sin movimiento (FECAEASinMovimientoInformar)
        AFIP Description: Método para informar comprobantes emitidos con CAEA (FECAEARegInformativo)
        AFIP Description: Método para consultar CAEA sin movimiento (FECAEASinMovimientoConsultar)
        AFIP Description: Recuperador de valores referenciales de códigos de Tipos de Alícuotas (FEParamGetTiposIva)
        AFIP Description: Recuperador de los puntos de venta asignados a Facturación Electrónica que soporten CAE y CAEA vía Web Services (FEParamGetPtosVenta)
        AFIP Description: Recuperador de cotización de moneda (FEParamGetCotizacion)
        AFIP Description: Recuperador de cantidad máxima de registros FECAESolicitar / FECAEARegInformativo (FECompTotXRequest)
        """

    def wsfe_get_status(self, cr, uid, ids, conn_id, context=None):
        """
        AFIP Description: Método Dummy para verificación de funcionamiento de infraestructura (FEDummy)
        """
        conn_obj = self.pool.get('wsafip.connection')

        r = {}
        for srv in self.browse(cr, uid, ids, context=context):
            # Ignore servers without code WSFE.
            if srv.code != 'wsfe': continue

            conn = conn_obj.browse(cr, uid, conn_id, context=context) 
            conn.login() # Login if nescesary.

            try:
                _logger.debug('Query AFIP Web service status')
                srvclient = Client(srv.url+'?WSDL', transport=HttpsTransport())
                response = srvclient.service.FEDummy()
            except Exception as e:
                _logger.error('AFIP Web service error!: (%i) %s' % (e[0], e[1]))
                raise osv.except_osv(_(u'AFIP Web service error'),
                                     _(u'System return error %i: %s') % (e[0], e[1]))
            r[srv.id] = (response.AuthServer,
                         response.AppServer,
                         response.DbServer)
        return r

    def wsfe_update_afip_concept_type(self, cr, uid, ids, conn_id, context=None):
        """
        Update concepts class.

        AFIP Description: Recuperador de valores referenciales de códigos de Tipos de Conceptos (FEParamGetTiposConcepto)
        """
        journal_obj = self.pool.get('afip.journal_class')
        conn_obj = self.pool.get('wsafip.connection')

        for srv in self.browse(cr, uid, ids, context=context):
            # Ignore servers without code WSFE.
            if srv.code != 'wsfe': continue

            # Take the connection, continue if connected or clockshifted
            conn = conn_obj.browse(cr, uid, conn_id, context=context) 
            conn.login() # Login if nescesary.
            if conn.state not in  [ 'connected', 'clockshifted' ]: continue

            # Build request
            auth=conn.get_auth()
            try:
                _logger.debug('Updating concept class from AFIP Web service')
                srvclient = Client(srv.url+'?WSDL', transport=HttpsTransport())
                response = srvclient.service.FEParamGetTiposConcepto(Auth=auth)

                # Take list of concept type
                concepttype_list = [
                    {'afip_code': ct.Id,
                     'name': ct.Desc,
                     'active': ct.FchHasta in [None, 'NULL'] }
                    for ct in response.ResultGet.ConceptoTipo ]
            except Exception as e:
                _logger.error('AFIP Web service error!: (%i) %s' % (e[0], e[1]))
                raise osv.except_osv(_(u'AFIP Web service error'),
                                     _(u'System return error %i: %s') % (e[0], e[1]))

            _update(self.pool, cr, uid,
                    'afip.concept_type',
                    concepttype_list,
                    can_create=True,
                    domain=[('afip_code','!=',0)]
                   )

        return


    def wsfe_update_journal_class(self, cr, uid, ids, conn_id, context=None):
        """
        Update journal class.

        AFIP Description: Recuperador de valores referenciales de códigos de Tipos de comprobante (FEParamGetTiposCbte)
        """
        journal_obj = self.pool.get('afip.journal_class')
        conn_obj = self.pool.get('wsafip.connection')

        for srv in self.browse(cr, uid, ids, context=context):
            # Ignore servers without code WSFE.
            if srv.code != 'wsfe': continue

            # Take the connection, continue if connected or clockshifted
            conn = conn_obj.browse(cr, uid, conn_id, context=context) 
            conn.login() # Login if nescesary.
            if conn.state not in  [ 'connected', 'clockshifted' ]: continue

            try:
                _logger.info('Updating journal class from AFIP Web service')
                srvclient = Client(srv.url+'?WSDL', transport=HttpsTransport())
                response = srvclient.service.FEParamGetTiposCbte(Auth=conn.get_auth())

                # Take list of journal class
                journalclass_list = [
                    { 'afip_code': c.Id,
                      'name': c.Desc,
                      'active': c.FchHasta  in [None, 'NULL']}
                    for c in response.ResultGet.CbteTipo
                ]
            except Exception as e:
                _logger.error('AFIP Web service error!: (%i) %s' % (e[0], e[1]))
                raise osv.except_osv(_(u'AFIP Web service error'),
                                     _(u'System return error %i: %s') % (e[0], e[1]))

            _update(self.pool, cr, uid,
                    'afip.journal_class',
                    journalclass_list,
                    can_create=False,
                    domain=[('afip_code','!=',0)]
                   )

        return

    def wsfe_update_document_type(self, cr, uid, ids, conn_id, context=None):
        """
        Update document type. This function must be called from connection model.

        AFIP Description: Recuperador de valores referenciales de códigos de Tipos de Documentos (FEParamGetTiposDoc)
        """
        doctype_obj = self.pool.get('afip.document_type')
        conn_obj = self.pool.get('wsafip.connection')

        for srv in self.browse(cr, uid, ids, context=context):
            # Ignore servers without code WSFE.
            if srv.code != 'wsfe': continue

            # Take the connection, continue if connected or clockshifted
            conn = conn_obj.browse(cr, uid, conn_id, context=context) 
            conn.login() # Login if nescesary.
            if conn.state not in  [ 'connected', 'clockshifted' ]: continue

            try:
                _logger.info('Updating document types from AFIP Web service')
                srvclient = Client(srv.url+'?WSDL', transport=HttpsTransport())
                response = srvclient.service.FEParamGetTiposDoc(Auth=conn.get_auth())

                # Take list of document types
                doctype_list = [
                    { 'afip_code': c.Id,
                      'name': c.Desc,
                      'code': c.Desc,
                      'active': c.FchHasta in [None, 'NULL'] }
                    for c in response.ResultGet.DocTipo
                ]
            except Exception as e:
                _logger.error('AFIP Web service error!: (%i) %s' % (e[0], e[1]))
                raise osv.except_osv(_(u'AFIP Web service error'),
                                     _(u'System return error %i: %s') % (e[0], e[1]))

            _update(self.pool, cr, uid,
                    'afip.document_type',
                    doctype_list,
                    can_create=True,
                    domain=[]
                   )

        return True
 
    def wsfe_update_optional_types(self, cr, uid, ids, conn_id, context=None):
        """
        Update optional types. This function must be called from connection model.

        AFIP Description: Recuperador de valores referenciales de códigos de Tipos de datos Opcionales (FEParamGetTiposOpcional)
        """
        conn_obj = self.pool.get('wsafip.connection')

        for srv in self.browse(cr, uid, ids, context=context):
            # Ignore servers without code WSFE.
            if srv.code != 'wsfe': continue

            # Take the connection, continue if connected or clockshifted
            conn = conn_obj.browse(cr, uid, conn_id, context=context) 
            conn.login() # Login if nescesary.
            if conn.state not in  [ 'connected', 'clockshifted' ]: continue

            try:
                _logger.info('Updating currency from AFIP Web service')
                srvclient = Client(srv.url+'?WSDL', transport=HttpsTransport())
                response = srvclient.service.FEParamGetTiposOpcional(Auth=conn.get_auth())

                # Take list of currency
                currency_list = [
                    { 'afip_code': c.Id,
                      'name': c.Desc,
                      'active': c.FchHasta in [None, 'NULL'] }
                    for c in response.ResultGet.OpcionalTipo
                ]
            except Exception as e:
                _logger.error('AFIP Web service error!: (%i) %s' % (e[0], e[1]))
                raise osv.except_osv(_(u'AFIP Web service error'),
                                     _(u'System return error %i: %s') % (e[0], e[1]))

            _update(self.pool, cr, uid,
                    'afip.optional_type',
                    currency_list,
                    can_create=True,
                    domain=[]
                   )
        return True
 
    def wsfe_update_currency(self, cr, uid, ids, conn_id, context=None):
        """
        Update currency. This function must be called from connection model.

        AFIP Description: Recuperador de valores referenciales de códigos de Tipos de Monedas (FEParamGetTiposMonedas) 
        """
        conn_obj = self.pool.get('wsafip.connection')

        for srv in self.browse(cr, uid, ids, context=context):
            # Ignore servers without code WSFE.
            if srv.code != 'wsfe': continue

            # Take the connection, continue if connected or clockshifted
            conn = conn_obj.browse(cr, uid, conn_id, context=context) 
            conn.login() # Login if nescesary.
            if conn.state not in  [ 'connected', 'clockshifted' ]: continue

            try:
                _logger.info('Updating currency from AFIP Web service')
                srvclient = Client(srv.url+'?WSDL', transport=HttpsTransport())
                response = srvclient.service.FEParamGetTiposMonedas(Auth=conn.get_auth())

                # Take list of currency
                currency_list = [
                    { 'afip_code': c.Id,
                      'name': c.Desc,
                      'active': c.FchHasta in [None, 'NULL'] }
                    for c in response.ResultGet.Moneda
                ]
            except Exception as e:
                _logger.error('AFIP Web service error!: (%i) %s' % (e[0], e[1]))
                raise osv.except_osv(_(u'AFIP Web service error'),
                                     _(u'System return error %i: %s') % (e[0], e[1]))

            _update(self.pool, cr, uid,
                    'res.currency',
                    currency_list,
                    can_create=False,
                    domain=[]
                   )
        return True

    def wsfe_update_tax(self, cr, uid, ids, conn_id, context=None):
        """
        Update taxes. This function must be called from connection model.

        AFIP Description: Recuperador de valores referenciales de códigos de Tipos de Tributos (FEParamGetTiposTributos)
        """
        conn_obj = self.pool.get('wsafip.connection')

        for srv in self.browse(cr, uid, ids, context=context):
            # Ignore servers without code WSFE.
            if srv.code != 'wsfe': continue

            # Take the connection, continue if connected or clockshifted
            conn = conn_obj.browse(cr, uid, conn_id, context=context) 
            conn.login() # Login if nescesary.
            if conn.state not in  [ 'connected', 'clockshifted' ]: continue

            _escape_ = lambda s: s.replace('%', '%%')

            try:
                _logger.info('Updating currency from AFIP Web service')
                srvclient = Client(srv.url+'?WSDL', transport=HttpsTransport())
                response = srvclient.service.FEParamGetTiposTributos(Auth=conn.get_auth())

                # Take list of taxes
                tax_list = [
                    { 'afip_code': c.Id,
                      'name': c.Desc }
                    for c in response.ResultGet.TributoTipo
                ]

                # Take IVA codes
                response = srvclient.service.FEParamGetTiposIva(Auth=conn.get_auth())
                tax_list.extend([
                    { 'afip_code': c.Id,
                      'name': "%s" % _escape_(c.Desc) }
                    for c in response.ResultGet.IvaTipo
                ])

            except Exception as e:
                _logger.error('AFIP Web service error!: (%i) %s' % (e[0], e[1]))
                raise osv.except_osv(_(u'AFIP Web service error'),
                                     _(u'System return error %i: %s') % (e[0], e[1]))

            tax_code_obj = self.pool.get('account.tax.code')


            for tc in tax_list:
                tax_code_ids = tax_code_obj.search(cr, uid, [('name','ilike', tc['name'])])
                _logger.debug("Tax '%s' match with %s" % (tc['name'], tax_code_ids))
                if tax_code_ids:
                    w = dict(tc)
                    del w['name']
                    tax_code_obj.write(cr, uid, tax_code_ids, w)

        return True

    def wsfe_get_last_invoice_number(self, cr, uid, ids, conn_id, ptoVta, cbteTipo, context=None):
        """
        Get last ID number from AFIP

        AFIP Description: Recuperador de ultimo valor de comprobante registrado (FECompUltimoAutorizado)
        """
        conn_obj = self.pool.get('wsafip.connection')

        r={}

        for srv in self.browse(cr, uid, ids, context=context):
            # Ignore servers without code WSFE.
            if srv.code != 'wsfe': continue

            # Take the connection
            conn = conn_obj.browse(cr, uid, conn_id, context=context) 
            conn.login() # Login if nescesary.
            if conn.state not in  [ 'connected', 'clockshifted' ]:
                r[srv.id] = False
                continue

            try:
                _logger.info('Take last invoice number from AFIP Web service (pto vta: %s, cbte tipo: %s)' % (ptoVta, cbteTipo))
                srvclient = Client(srv.url+'?WSDL', transport=HttpsTransport())
                response = srvclient.service.FECompUltimoAutorizado(Auth=conn.get_auth(), PtoVta=ptoVta, CbteTipo=cbteTipo)

            except Exception as e:
                _logger.error('AFIP Web service error!: (%i) %s' % (e[0], e[1]))
                raise osv.except_osv(_(u'AFIP Web service error'),
                                     _(u'System return error %i: %s\n'
                                       u'Pueda que esté intente realizar esta operación'
                                       u'desde el servidor de homologación.'
                                       u'Intente desde el servidor de producción.') % (e[0], e[1]))

            if hasattr(response, 'Errors'):
                for e in response.Errors.Err:
                    code = e.Code
                    _logger.error('AFIP Web service error!: (%i) %s' % (e.Code, e.Msg))
                r[srv.id] = False
            else:
                r[srv.id] = int(response.CbteNro)
        return r

    def wsfe_get_cae(self, cr, uid, ids, conn_id, invoice_request, context=None):
        """
        Get CAE.

        AFIP Description: Método de autorización de comprobantes electrónicos por CAE (FECAESolicitar)
        """
        conn_obj = self.pool.get('wsafip.connection')
        r = {}

        for srv in self.browse(cr, uid, ids, context=context):
            # Ignore servers without code WSFE.
            if srv.code != 'wsfe': continue

            # Take the connection
            conn = conn_obj.browse(cr, uid, conn_id, context=context) 
            conn.login() # Login if nescesary.
            if conn.state not in  [ 'connected', 'clockshifted' ]: continue

            _logger.info('Get CAE from AFIP Web service')

            auth = conn.get_auth()
            try:
                srvclient = Client(srv.url+'?WSDL', transport=HttpsTransport())
                first = invoice_request.keys()[0]
                response = srvclient.service.FECAESolicitar(Auth=auth,
                    FeCAEReq = [{
                        'FeCabReq':{
                            'CantReg': len(invoice_request),
                            'PtoVta': invoice_request[first]['PtoVta'],
                            'CbteTipo': invoice_request[first]['CbteTipo'],
                        },
                        'FeDetReq': [
                            { 'FECAEDetRequest': dict(
                                [ (k, v) for k,v in req.iteritems()
                                 if k not in ['CantReg', 'PtoVta', 'CbteTipo'] ] ) }
                            for req in invoice_request.itervalues()
                        ],
                    }]
                )
            except WebFault as e:
                _logger.error('AFIP Web service error!: %s' % (e[0]))
                raise osv.except_osv(_(u'AFIP Web service error'),
                                     _(u'System return error: %s') % e[0])
            except Exception as e:
                _logger.error('AFIP Web service error!: (%i) %s' % (e[0], e[1]))
                raise osv.except_osv(_(u'AFIP Web service error'),
                                     _(u'System return error %i: %s') % (e[0], e[1]))
                                     
            soapRequest = [{ 'FeCabReq':{ 'CantReg': len(invoice_request), 'PtoVta': invoice_request[first]['PtoVta'], 'CbteTipo': invoice_request[first]['CbteTipo'],}, 'FeDetReq': [{ 'FECAEDetRequest': dict([ (k, v) for k,v in req.iteritems() if k not in ['CantReg', 'PtoVta', 'CbteTipo'] ] ) } for req in invoice_request.itervalues()], }]

            try: 
                common_error = [ (e.Code, unicode(e.Msg)) for e in response.Errors[0] ] if response.FeCabResp.Resultado in ["P", "R"] and hasattr(response, 'Errors') else []
            else:
                import pdb; pdb.set_trace()

            _logger.error('Request error: %s' % (common_error,))

            for resp in response.FeDetResp.FECAEDetResponse:
                if resp.Resultado == 'R':
                    # Existe Error!
                    _logger.error('Invoice error: %s' % (resp,))
                    r[int(resp.CbteDesde)]={
                        'CbteDesde': resp.CbteDesde,
                        'CbteHasta': resp.CbteHasta,
                        'Observaciones': [ (o.Code, unicode(o.Msg)) for o in resp.Observaciones.Obs ]
                                if hasattr(resp,'Observaciones') else [],
                        'Errores': common_error + [ (e.Code, unicode(e.Msg)) for e in response.Errors.Err ]
                                if hasattr(response, 'Errors') else [],
                    }
                else:
                    # Todo bien!
                    r[int(resp.CbteDesde)]={
                        'CbteDesde': resp.CbteDesde,
                        'CbteHasta': resp.CbteHasta,
                        'CAE': resp.CAE,
                        'CAEFchVto': resp.CAEFchVto,
                        'Request': soapRequest,
                        'Response': response,
                    }
        return r

    def wsfe_query_invoice(self, cr, uid, ids, conn_id, cbteTipo, cbteNro, ptoVta, context=None):
        """
        Query for invoice stored by AFIP Web service.

        AFIP Description: Método para consultar Comprobantes Emitidos y su código (FECompConsultar)
        """
        conn_obj = self.pool.get('wsafip.connection')
        r = {}

        for srv in self.browse(cr, uid, ids, context=context):
            # Ignore servers without code WSFE.
            if srv.code != 'wsfe': continue

            # Take the connection
            conn = conn_obj.browse(cr, uid, conn_id, context=context) 
            conn.login() # Login if nescesary.

            try:
                _logger.info('Query for invoice stored by AFIP Web service')
                srvclient = Client(srv.url+'?WSDL', transport=HttpsTransport())
                response = srvclient.service.FECompConsultar(Auth=conn.get_auth(),
                                                                 FeCompConsReq={
                                                                     'CbteTipo': cbteTipo,
                                                                     'CbteNro': cbteNro,
                                                                     'PtoVta': ptoVta,
                                                                 })
            except Exception as e:
                _logger.error('AFIP Web service error!: (%i) %s' % (e[0], e[1]))
                raise osv.except_osv(_(u'AFIP Web service error'),
                                     _(u'System return error %i: %s\n'
                                       u'Pueda que esté intente realizar esta operación'
                                       u'desde el servidor de homologación.'
                                       u'Intente desde el servidor de producción.') % (e[0], e[1]))

            if hasattr(response, 'Errors'):
                for e in response.Errors.Err:
                    code = e.Code
                    _logger.error('AFIP Web service error!: (%i) %s' % (e.Code, e.Msg))
                r[srv.id] = False
            else:
                r[srv.id] = { 
                    'Concepto': response.ResultGet.Concepto,
                    'DocTipo': response.ResultGet.DocTipo,
                    'DocNro': response.ResultGet.DocNro,
                    'CbteDesde': response.ResultGet.CbteDesde,
                    'CbteHasta': response.ResultGet.CbteHasta,
                    'CbteFch': response.ResultGet.CbteFch,
                    'ImpTotal': response.ResultGet.ImpTotal,
                    'ImpTotConc': response.ResultGet.ImpTotConc,
                    'ImpNeto': response.ResultGet.ImpNeto,
                    'ImpOpEx': response.ResultGet.ImpOpEx,
                    'ImpTrib': response.ResultGet.ImpTrib,
                    'ImpIVA': response.ResultGet.ImpIVA,
                    'FchServDesde': response.ResultGet.FchServDesde,
                    'FchServHasta': response.ResultGet.FchServHasta,
                    'FchVtoPago': response.ResultGet.FchVtoPago,
                    'MonId': response.ResultGet.MonId,
                    'MonCotiz': response.ResultGet.MonCotiz,
                    'Resultado': response.ResultGet.Resultado,
                    'CodAutorizacion': response.ResultGet.CodAutorizacion,
                    'EmisionTipo': response.ResultGet.EmisionTipo,
                    'FchVto': response.ResultGet.FchVto,
                    'FchProceso': response.ResultGet.FchProceso,
                    'PtoVta': response.ResultGet.PtoVta,
                    'CbteTipo': response.ResultGet.CbteTipo,
                }

        return r

wsafip_server()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
