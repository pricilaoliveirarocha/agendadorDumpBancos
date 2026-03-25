# 🗂️ Dumps automaticos

Automatiza o dump de dois bancos de um database selecionado e envia os arquivos para o Notion via integracao de API.

## ✨ Sobre
Este app foi criado para reduzir trabalho manual no backup e compartilhamento de dumps entre a equipe. 
Ele:
- Acessa a VPN em OpenVPN Connect
- recebe um database de entrada
- gera o dump de dois bancos especificados na configuração
- publica os arquivos em .zip no Notion automaticamente

## ⚙️ Como funciona
1. Seleciona o database.
2. Gera os dumps dos dois bancos.
3. Envia os arquivos para o Notion via integração.

## ✅ Requisitos
- Integração ativa no Notion para permitir o envio automático.
- Configurações corretas em `.config`
- Rodar `pip install -r requirements.txt`