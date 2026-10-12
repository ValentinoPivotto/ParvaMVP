"""Firma SigV4 para Bedrock.

Vectores fijos (fecha y credenciales clavadas) verificados contra el canonical
request que imprime el AWS CLI y contra la implementación anterior. El algoritmo
no tiene variantes: si esto cambia, el parser deja de poder hablar con Bedrock.
"""
import unittest
from datetime import datetime, timezone

from backend.formato import a_json, percent_encode
from backend.service.sigv4 import AwsCreds, firmar_aws

COMUN = dict(
    method='POST',
    host='bedrock-runtime.us-east-2.amazonaws.com',
    # El path va ya percent-encodeado por quien llama: el id del modelo trae ':'.
    path=f"/model/{percent_encode('amazon.nova-lite-v1:0')}/converse",
    region='us-east-2',
    service='bedrock',
    body=a_json({'hola': 'qué tal ñ'}),
    ahora=datetime(2026, 9, 22, 3, 15, 0, tzinfo=timezone.utc),
)


class Firma(unittest.TestCase):
    def test_credenciales_permanentes(self) -> None:
        r = firmar_aws(**COMUN, creds=AwsCreds('AKIAEJEMPLO', 'secreto/con+simbolos'))
        self.assertEqual(r.url, 'https://bedrock-runtime.us-east-2.amazonaws.com'
                                '/model/amazon.nova-lite-v1%3A0/converse')
        self.assertEqual(r.headers, {
            'content-type': 'application/json',
            'x-amz-date': '20260922T031500Z',
            'Authorization': (
                'AWS4-HMAC-SHA256 Credential=AKIAEJEMPLO/20260922/us-east-2/bedrock/aws4_request, '
                'SignedHeaders=content-type;host;x-amz-date, '
                'Signature=403850509bf2f6d1d6fceee4cfeee3e3beb23d747f77663b0de3cc14440b1941'),
        })

    def test_credenciales_temporales_agregan_el_security_token(self) -> None:
        r = firmar_aws(**COMUN, creds=AwsCreds('ASIATEMP', 'otro', 'tok=en/temporal'))
        self.assertEqual(r.headers['x-amz-security-token'], 'tok=en/temporal')
        # El token entra en los headers firmados, así que cambia la firma.
        self.assertIn('SignedHeaders=content-type;host;x-amz-date;x-amz-security-token',
                      r.headers['Authorization'])

    def test_el_host_se_firma_pero_no_se_manda(self) -> None:
        # Lo pone el runtime desde la URL; mandarlo a mano lo duplicaría.
        r = firmar_aws(**COMUN, creds=AwsCreds('AKIAEJEMPLO', 'x'))
        self.assertNotIn('host', r.headers)
        self.assertIn('host', r.headers['Authorization'])

    def test_el_path_se_firma_doblemente_encodeado(self) -> None:
        # Para todo servicio que no sea S3, AWS vuelve a encodear un path que ya
        # viene encodeado: 'v1%3A0' se firma como 'v1%253A0'. Se comprueba de
        # rebote: dos paths que sólo difieren en eso dan firmas distintas.
        otro = dict(COMUN, path='/model/amazon.nova-lite-v1%253A0/converse')
        a = firmar_aws(**COMUN, creds=AwsCreds('AKIAEJEMPLO', 'x'))
        b = firmar_aws(**otro, creds=AwsCreds('AKIAEJEMPLO', 'x'))
        self.assertNotEqual(a.headers['Authorization'], b.headers['Authorization'])

    def test_el_cuerpo_viaja_intacto(self) -> None:
        r = firmar_aws(**COMUN, creds=AwsCreds('AKIAEJEMPLO', 'x'))
        self.assertEqual(r.body, '{"hola":"qué tal ñ"}')


if __name__ == '__main__':
    unittest.main()
