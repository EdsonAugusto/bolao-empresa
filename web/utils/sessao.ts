/**
 * A regra que decide quando uma sessão acaba.
 *
 * Por que isto é uma função, e não um `if` no meio do código
 * ----------------------------------------------------------
 * Esta decisão já foi escrita errado duas vezes, em dois lugares diferentes —
 * e das duas o sintoma foi o mesmo: o aplicativo instalado abria pedindo
 * e-mail e senha, sem nada explicando por quê.
 *
 * O erro é sempre o mesmo raciocínio: "a requisição falhou, então a sessão
 * morreu". Não morreu. Celular sem sinal, wifi trocando de rede, API
 * reiniciando durante uma atualização — nenhuma dessas coisas diz nada sobre a
 * credencial. A única coisa que encerra uma sessão é o servidor RECUSANDO a
 * credencial, e para isso ele precisa ter respondido.
 *
 * Com a regra num lugar só, o terceiro lugar que precisar dela chama esta
 * função em vez de reescrever o `if`.
 */

/**
 * O servidor recusou a credencial, ou foi só a rede?
 *
 * @param status Código HTTP. **Zero** é o que o cliente usa quando não houve
 *   resposta nenhuma — DNS que não resolveu, conexão recusada, tempo esgotado.
 */
export function credencialFoiRecusada(status: number): boolean {
  // 401 é "não autenticado" e 403 é "autenticado e sem direito". Os dois
  // significam que o servidor olhou o token e disse não.
  //
  // 5xx NÃO entra: um erro interno da API é problema dela, e derrubar a sessão
  // de todo mundo a cada deploy com defeito seria transformar um susto em
  // vinte pessoas digitando senha.
  return status === 401 || status === 403
}
