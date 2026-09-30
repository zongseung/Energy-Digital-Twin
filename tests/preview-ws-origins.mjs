import assert from 'node:assert/strict';
import http from 'node:http';

const base = process.argv[2] || 'http://127.0.0.1:8090';
function handshake(path, origin) {
  return new Promise((resolve, reject) => {
    const request = http.get(new URL(path, base), { headers: {
      Connection: 'Upgrade', Upgrade: 'websocket', 'Sec-WebSocket-Version': '13',
      'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==', Origin: origin,
    } });
    request.setTimeout(3000, () => request.destroy(new Error('WebSocket handshake timed out')));
    request.on('error', reject);
    request.on('response', response => { response.resume(); resolve(response.statusCode); });
    request.on('upgrade', (response, socket) => { socket.destroy(); resolve(response.statusCode); });
  });
}
for (const path of ['/api/v1/jeju/wind/ws', '/api/v1/jeju/ws']) {
  for (const origin of ['http://localhost:8080', 'http://127.0.0.1:8080', 'http://localhost:18080', 'http://127.0.0.1:18080']) {
    const status = await handshake(path, origin);
    assert.equal(status, 101, origin + ' must receive live updates on ' + path);
  }
  assert.equal(await handshake(path, 'https://untrusted.example'), 403);
}
console.log('PASS: both preview WebSockets accept all four advertised local origins and reject foreign origins');
