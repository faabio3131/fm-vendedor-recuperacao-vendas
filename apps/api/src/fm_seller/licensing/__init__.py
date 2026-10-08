"""Licenças do Billing Central do FM Command (ADR-0005, contrato `fmcc.license.v1`).

O AtendeVendeIA **aplica** a licença que o Command determina; não cobra, não fala com gateway e não
decide estado comercial das assinaturas que o Command governa. Tudo aqui nasce DESLIGADO
(`FM_FMCOMMAND_MODE=off`) e não toca nas integrações diretas de Cakto e Hotmart.
"""
