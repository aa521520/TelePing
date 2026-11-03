import asyncio
import ssl
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@asynccontextmanager
async def tls_site(directory, *, expired=False):
    now = int(time.time())
    instant = datetime.fromtimestamp(now, UTC)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "example.test")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(instant - timedelta(days=2))
        .not_valid_after(instant + timedelta(days=-1 if expired else 10))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("example.test")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "test-cert.pem", directory / "test-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    sni = []
    context.set_servername_callback(lambda connection, host, ctx: sni.append(host))

    async def handle(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            body = b"<body>Secure visible content</body>"
            writer.write(
                f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
            )
            await writer.drain()
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=context)
    async with server:
        yield {"port": server.sockets[0].getsockname()[1], "cafile": str(cert_path), "sni": sni, "now": now}
