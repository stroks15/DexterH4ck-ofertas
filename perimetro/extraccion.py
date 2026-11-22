from __future__ import annotations
import json,re
from html.parser import HTMLParser
from urllib.parse import urljoin
from core.price_sanitizer import parse_price, sanear_par_precios

class _Node:
    def __init__(self,tag,attrs,parent=None):self.tag=tag;self.attrs=dict(attrs);self.children=[];self.text=[];self.parent=parent
    def all(self):
        yield self
        for child in self.children:
            yield from child.all()
class _Tree(HTMLParser):
    VOID={"img","input","br","hr","meta","link","source"}
    def __init__(self,source):super().__init__(convert_charrefs=True);self.root=_Node("root",[]);self.stack=[self.root];self.feed(source or "")
    def handle_starttag(self,tag,attrs):
        n=_Node(tag,attrs,self.stack[-1]);self.stack[-1].children.append(n)
        if tag not in self.VOID:self.stack.append(n)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:self.stack=self.stack[:i];break
    def handle_data(self,data):self.stack[-1].text.append(data)
def _text(n):return " ".join(n.text+[_text(c) for c in n.children]).strip()
def _match(n,selector):
    s=(selector or "").strip().split()[0]
    if not s:return False
    if s.startswith("["):
        m=re.match(r"\[([\w:-]+)(?:\*=|=)?['\"]?([^'\"\]]*)",s)
        return bool(m and m.group(1) in n.attrs and (not m.group(2) or m.group(2) in str(n.attrs[m.group(1)])))
    parts=s.split(":",1)[0].split("."); tag=parts[0] if parts[0] and parts[0]!="*" else ""
    classes=set(str(n.attrs.get("class","")).split())
    return (not tag or n.tag==tag) and all(c in classes for c in parts[1:])
def _select(nodes,selector):return [n for n in nodes if _match(n,selector)]
def select_cards(html,selectors,page_url,store):
    tree=_Tree(html); nodes=list(tree.root.all()); cards=_select(nodes,selectors.get("card","")); out=[]
    if not cards and selectors.get("card"):return []
    for card in cards:
        ns=list(card.all()); titles=_select(ns,selectors.get("title","")); prices=_select(ns,selectors.get("price","")); links=_select(ns,selectors.get("link",""))
        title=_text(titles[0]) if titles else ""; pnode=prices[0] if prices else None
        price=parse_price((pnode.attrs.get("content") or pnode.attrs.get("data-price") or _text(pnode)) if pnode else None)
        url=urljoin(page_url,(links[0].attrs.get("href","") if links else ""))
        if title and price and url.startswith(("http://","https://")):
            previous=None
            for node in ns:
                if node.tag in ("del","s","strike"):
                    previous=parse_price(_text(node))
                    if previous and previous>price:break
                    previous=None
            price,previous,_=sanear_par_precios(price,previous,title)
            out.append({"tienda":store,"titulo":title,"url":url,"precio_actual":price,"precio_anterior":previous,"descuento":round((1-price/previous)*100) if previous and previous>price else 0,"origen_link":"perimetro_html","tipo_fuente":"ONLINE"})
    return out

def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)
def structured(data,store,base):
    out=[]
    for x in _walk(data):
        name=x.get("name") or x.get("productName") or x.get("title"); url=x.get("url") or x.get("link") or x.get("productUrl"); offers=x.get("offers",{})
        if isinstance(offers,list):offers=offers[0] if offers else {}
        offers=offers if isinstance(offers,dict) else {}
        price=parse_price(offers.get("price") or x.get("price") or x.get("currentPrice")); old=parse_price(x.get("highPrice") or x.get("listPrice") or x.get("previousPrice"))
        if name and price and url:
            actual,old,_=sanear_par_precios(price,old,name);out.append({"tienda":store,"titulo":str(name).strip(),"url":urljoin(base,str(url)),"precio_actual":actual,"precio_anterior":old,"descuento":round((1-actual/old)*100) if old and actual and old>actual else 0,"origen_link":"perimetro_structured","tipo_fuente":"ONLINE"})
    return out

def extraer(strategy,body,store,base,selectors=None):
    if strategy in ("jsonld","nextdata"):
        out=[]
        if strategy=="jsonld":
            for script in re.findall(r"<script[^>]+type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>",body or "",re.I|re.S):
                try:out.extend(structured(json.loads(script),store,base))
                except (ValueError,TypeError):pass
        else:
            m=re.search(r"<script[^>]+id=['\"]__NEXT_DATA__['\"][^>]*>(.*?)</script>",body or "",re.I|re.S)
            if m:
                try:out.extend(structured(json.loads(m.group(1)),store,base))
                except (ValueError,TypeError):pass
        return out
    if strategy in ("html","amazon","sfcc","vtex"):return select_cards(body,selectors or {},base,store)
    if strategy=="graphql":
        try:return structured(json.loads(body),store,base)
        except (ValueError,TypeError):return []
    return []
