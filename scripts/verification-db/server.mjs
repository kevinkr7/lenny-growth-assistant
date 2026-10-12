// Explicit development/test fallback only. Production uses native PostgreSQL in Compose.
import { PGlite } from '@electric-sql/pglite'
import { vector } from '@electric-sql/pglite-pgvector'
import { PGLiteSocketServer } from '@electric-sql/pglite-socket'
import { createServer, connect } from 'node:net'
const port = Number(process.env.VERIFICATION_PORT || 55432)
const db = await PGlite.create({ dataDir: process.env.VERIFICATION_DB_DIR || '../../data/verification-db-v2', extensions: { vector } })
const server = new PGLiteSocketServer({ db, port: port + 1, host: '127.0.0.1', maxConnections: 16 })
await server.start()
// PGlite's socket adapter does not close on PostgreSQL Terminate. asyncpg waits for
// that EOF. This test-only proxy supplies it; native PostgreSQL needs no shim.
const proxy = createServer(client => {
  const upstream = connect(port + 1, '127.0.0.1')
  client.on('data', data => {
    if (data.length === 5 && data[0] === 0x58 && data.readUInt32BE(1) === 4) {
      client.end(); upstream.end(); return
    }
    upstream.write(data)
  })
  upstream.on('data', data => client.write(data))
  client.on('end', () => upstream.end())
  upstream.on('end', () => client.end())
  client.on('error', () => upstream.destroy())
  upstream.on('error', () => client.destroy())
})
proxy.listen(port, '127.0.0.1')
console.log(`Verification PostgreSQL (PGlite, multiplexed single backend) on 127.0.0.1:${port}`)
process.on('SIGINT', async () => { proxy.close(); await server.stop(); await db.close(); process.exit(0) })
