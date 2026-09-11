"""Bounded host HTTP fetches for the in-progress original-browser transport.

This module does not render pages or mark guest network services ready.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import socket
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, HTTPSHandler, HTTPRedirectHandler, build_opener

USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.4 Safari/605.1.15'

@dataclass(frozen=True)
class WebResponse:
    url: str
    status: int
    content_type: str
    body: bytes


def validate_url(url):
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname:
        raise ValueError('Only HTTP and HTTPS URLs are supported')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError('Credentials in URLs are not supported')
    return url


class Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HostWeb:
    def __init__(self, *, timeout=10, max_bytes=2*1024*1024):
        if timeout<=0 or max_bytes<=0:
            raise ValueError('Positive timeout and response limit required')
        self.timeout=timeout
        self.max_bytes=max_bytes
        self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='w800-web')
        self.pending=set()
        self.closed=False
        # Workspace convention: use the local inspection proxy when reachable.
        try:
            with socket.create_connection(('127.0.0.1',8080),timeout=.15): pass
            self.proxy='http://127.0.0.1:8080'
        except OSError:
            self.proxy=None
        context=ssl._create_unverified_context() if self.proxy else ssl.create_default_context()
        self.opener=build_opener(ProxyHandler({'http':self.proxy,'https':self.proxy} if self.proxy else {}),HTTPSHandler(context=context),Redirects())

    def fetch(self,url):
        if self.closed: raise RuntimeError('Host web transport is closed')
        validate_url(url)
        self.pending={f for f in self.pending if not f.done()}
        if len(self.pending)>=8: raise RuntimeError('Host web request queue is full')
        future=self.executor.submit(self._fetch,url)
        self.pending.add(future)
        return future

    def _fetch(self,url):
        request=Request(url,headers={'User-Agent':USER_AGENT,'Accept-Encoding':'identity'})
        deadline=time.monotonic()+self.timeout
        try: response=self.opener.open(request,timeout=self.timeout)
        except HTTPError as error: response=error
        with response:
            parts=[];total=0
            while True:
                if time.monotonic()>deadline: raise TimeoutError('Host web response timed out')
                chunk=response.read1(min(65536,self.max_bytes+1-total))
                if not chunk: break
                total+=len(chunk)
                if total>self.max_bytes: raise ValueError('Web response exceeds the phone transfer limit')
                parts.append(chunk)
            return WebResponse(response.geturl(),response.status,response.headers.get('Content-Type','application/octet-stream'),b''.join(parts))

    def close(self):
        self.closed=True
        for future in self.pending: future.cancel()
        self.executor.shutdown(wait=False,cancel_futures=True)

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
