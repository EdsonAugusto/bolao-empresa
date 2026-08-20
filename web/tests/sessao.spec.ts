import { describe, expect, it } from 'vitest'

import { credencialFoiRecusada } from '~/utils/sessao'

/**
 * Estes testes existem por causa de um bug que voltou.
 *
 * Duas vezes o código apagou os cookies da sessão porque uma requisição
 * falhou — sem olhar o motivo. O sintoma foi o mesmo das duas: o aplicativo
 * instalado no celular abria pedindo e-mail e senha, toda vez, e não havia
 * nada em tela nem em log explicando.
 *
 * A fronteira que estes casos fixam: só o servidor RESPONDENDO que a
 * credencial não vale encerra a sessão. Qualquer coisa que impediu a resposta
 * de chegar é rede, e rede volta.
 */

describe('credencialFoiRecusada', () => {
  it('encerra quando o servidor recusa o token', () => {
    expect(credencialFoiRecusada(401)).toBe(true)
    expect(credencialFoiRecusada(403)).toBe(true)
  })

  it('NÃO encerra quando não houve resposta', () => {
    // Zero é o que o cliente registra quando a requisição não chegou a lugar
    // nenhum: sem sinal, DNS que não resolveu, conexão recusada. Foi este o
    // caso que deslogava quem abria o app fora de cobertura.
    expect(credencialFoiRecusada(0)).toBe(false)
  })

  it('NÃO encerra quando a API está com problema', () => {
    // Um deploy com defeito derrubaria a sessão de todo o grupo de uma vez, e
    // cada pessoa teria de digitar a senha de novo por causa de um erro que
    // não é dela.
    for (const status of [500, 502, 503, 504]) {
      expect(credencialFoiRecusada(status)).toBe(false)
    }
  })

  it('NÃO encerra em erro de pedido nem em sucesso', () => {
    // 400 e 422 são "o que você mandou está errado", 404 é "não existe", 429 é
    // "devagar". Nenhum diz que a credencial deixou de valer.
    for (const status of [200, 201, 400, 404, 409, 422, 429]) {
      expect(credencialFoiRecusada(status)).toBe(false)
    }
  })
})
