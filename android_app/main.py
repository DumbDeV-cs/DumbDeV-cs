"""Kivy Android build of Stealth Weather Pro."""
__version__ = "2.0.0"
import json, math, threading
from datetime import datetime
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle, Rectangle
from kivy.metrics import dp, sp
from kivy.properties import ListProperty, NumericProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from weather_core import Place, condition, emoji, forecast, get_json, search_places
from vault_store import VaultStore

BG=(.035,.075,.16,1); CARD=(.10,.16,.28,1); CARD2=(.12,.20,.34,1)
WHITE=(.96,.98,1,1); MUTED=(.70,.78,.90,1); BLUE=(.32,.65,1,1)

def text(s="", size=14, color=WHITE, bold=False, h=None, align="left"):
    w=Label(text=str(s),font_size=sp(size),color=color,bold=bold,halign=align,valign="middle",
            size_hint_y=None if h else 1,height=dp(h) if h else None)
    w.bind(size=lambda i,v:setattr(i,"text_size",(v[0],None)))
    return w

class Card(BoxLayout):
    def __init__(self,*a,**kw):
        c=kw.pop("card_color",CARD); super().__init__(*a,**kw)
        with self.canvas.before:
            Color(*c); self.shape=RoundedRectangle(pos=self.pos,size=self.size,radius=[dp(20)])
        self.bind(pos=lambda i,v:setattr(i.shape,"pos",v),size=lambda i,v:setattr(i.shape,"size",v))

class Art(Widget):
    kind=StringProperty("clear"); day=NumericProperty(1); phase=NumericProperty(0)
    def __init__(self,**kw):
        super().__init__(**kw); self.bind(pos=self.draw,size=self.draw,kind=self.draw,day=self.draw)
        Clock.schedule_interval(self.tick,1/12)
    def tick(self,dt): self.phase=(self.phase+.06)%math.tau; self.draw()
    def draw(self,*_):
        if self.width<2:return
        self.canvas.clear(); x,y=self.center; s=min(self.width,self.height)/150
        with self.canvas:
            if not self.day:
                for i,(dx,dy) in enumerate([(-48,42),(45,44),(54,-10),(-40,-35),(8,-48)]):
                    a=.35+.4*(.5+.5*math.sin(self.phase*2+i)); Color(.85,.91,1,a)
                    Ellipse(pos=(x+dx*s-2*s,y+dy*s-2*s),size=(4*s,4*s))
            if self.kind=="clear" and self.day:
                pulse=.5+.5*math.sin(self.phase)
                for r in (54,47,40):
                    Color(1,.72,.18,.06+.045*pulse); Ellipse(pos=(x-r*s,y-r*s),size=(2*r*s,2*r*s))
                for i in range(12):
                    a=self.phase*.35+i*math.tau/12; r1=36*s; r2=(46+pulse*4)*s
                    Color(1,.82,.29,.95); Line(points=[x+r1*math.cos(a),y+r1*math.sin(a),x+r2*math.cos(a),y+r2*math.sin(a)],width=dp(2.4),cap="round")
                Color(1,.78,.17,1); Ellipse(pos=(x-26*s,y-26*s),size=(52*s,52*s))
                Color(1,.91,.48,1); Ellipse(pos=(x-17*s,y-18*s),size=(32*s,32*s))
            elif self.kind=="clear":
                Color(.86,.9,1,1); Ellipse(pos=(x-25*s,y-25*s),size=(50*s,50*s))
                Color(.10,.16,.28,1); Ellipse(pos=(x-7*s,y-32*s),size=(45*s,45*s))
            else:
                self.cloud(x+math.sin(self.phase)*5*s,y+10*s,s,(.88,.94,1,1))
                if self.kind in ("rain","storm"):
                    for i in range(7):
                        p=(self.phase*24+i*17)%48; xx=x-30*s+i*10*s+p*.1*s; yy=y-4*s-p*s
                        Color(.55,.8,1,max(.25,1-p/60)); Line(points=[xx,yy,xx-3*s,yy-8*s],width=2,cap="round")
                elif self.kind=="snow":
                    for i in range(8):
                        p=(self.phase*8+i*11)%45; xx=x-32*s+i*9*s+math.sin(self.phase+i)*3*s; yy=y-p*s
                        Color(.92,.96,1,.9); Ellipse(pos=(xx-2*s,yy-2*s),size=(4*s,4*s))
                elif self.kind=="storm" and int(self.phase*9)%11<2:
                    Color(1,.88,.3,1); Line(points=[x+7*s,y,x-3*s,y-14*s,x+3*s,y-14*s,x-8*s,y-34*s],width=4,cap="round")
                elif self.kind=="fog":
                    for i in range(3):
                        yy=y-(i+1)*10*s+math.sin(self.phase+i)*4*s; Color(.88,.94,1,.75-i*.12)
                        Line(points=[x-36*s,yy,x+36*s,yy],width=4,cap="round")
    def cloud(self,x,y,s,c):
        Color(.28,.4,.58,.55); Ellipse(pos=(x-38*s,y-24*s),size=(78*s,31*s))
        Color(*c); Ellipse(pos=(x-37*s,y-20*s),size=(39*s,32*s)); Ellipse(pos=(x-21*s,y-31*s),size=(49*s,49*s))
        Ellipse(pos=(x+3*s,y-23*s),size=(38*s,36*s)); RoundedRectangle(pos=(x-24*s,y-25*s),size=(50*s,23*s),radius=[dp(8)])

class Trend(Widget):
    values=ListProperty([])
    def __init__(self,**kw):
        super().__init__(**kw); self.i=0; self.bind(pos=self.draw,size=self.draw,values=self.draw); Clock.schedule_interval(self.advance,.3)
    def advance(self,dt):
        if self.values:self.i=(self.i+1)%len(self.values); self.draw()
    def draw(self,*_):
        self.canvas.clear()
        if len(self.values)<2:return
        lo,hi=min(self.values),max(self.values); span=max(hi-lo,1)
        pts=[(self.x+dp(8)+i*(self.width-dp(16))/(len(self.values)-1),self.y+dp(12)+(v-lo)/span*(self.height-dp(24))) for i,v in enumerate(self.values)]
        with self.canvas:
            Color(.3,.63,1,.13); Line(points=[pts[0][0],self.y+dp(2)]+[c for p in pts for c in p]+[pts[-1][0],self.y+dp(2)],close=True)
            Color(.31,.68,1,.3); Line(points=[c for p in pts for c in p],width=7,cap="round",joint="round")
            Color(.78,.9,1,1); Line(points=[c for p in pts for c in p],width=2,cap="round",joint="round")
            px,py=pts[self.i]; Color(.47,.78,1,.35); Ellipse(pos=(px-dp(10),py-dp(10)),size=(dp(20),dp(20)))
            Color(1,1,1,1); Ellipse(pos=(px-dp(4),py-dp(4)),size=(dp(8),dp(8)))

class Range(Widget):
    low=NumericProperty(0); high=NumericProperty(0); minimum=NumericProperty(0); maximum=NumericProperty(1)
    def __init__(self,**kw):
        super().__init__(**kw); self.bind(pos=self.draw,size=self.draw,low=self.draw,high=self.draw,minimum=self.draw,maximum=self.draw)
    def draw(self,*_):
        self.canvas.clear()
        if self.width<2:return
        span=max(self.maximum-self.minimum,1); x1=self.x+dp(4)+(self.low-self.minimum)/span*(self.width-dp(8)); x2=self.x+dp(4)+(self.high-self.minimum)/span*(self.width-dp(8))
        with self.canvas:
            Color(.22,.32,.46,1); Line(points=[self.x+dp(4),self.center_y,self.right-dp(4),self.center_y],width=5,cap="round")
            Color(1,.71,.27,1); Line(points=[x1,self.center_y,x2,self.center_y],width=5,cap="round")

class WeatherApp(App):
    def build(self):
        Window.clearcolor=BG; self.units="metric"; self.current_place=None; self.current_data=None
        self.last_file=self.user_data_dir+"/last_place.json"; self.store=VaultStore(self.user_data_dir)
        self.suggest_event=None; self.req_seq=0; self.suggest_seq=0; self.gps_received=False
        self.root_box=BoxLayout(orientation="vertical")
        with self.root_box.canvas.before:
            Color(*BG); self.background=Rectangle(pos=self.root_box.pos,size=self.root_box.size)
        self.root_box.bind(pos=lambda i,v:setattr(self.background,"pos",v),size=lambda i,v:setattr(self.background,"size",v))
        self.show_weather(); self.restore_last()
        if not self.store.config.exists():Clock.schedule_once(lambda dt:self.setup_code(),.8)
        return self.root_box
    def btn(self,s,fn,w=None,color=CARD2,size=13):
        b=Button(text=s,font_size=sp(size),color=WHITE,size_hint_x=None if w else 1,width=dp(w) if w else 1,size_hint_y=None,height=dp(44),on_release=lambda *_:fn())
        b.background_normal=""; b.background_color=color; return b
    def show_weather(self):
        self.root_box.clear_widgets(); scroll=ScrollView(do_scroll_x=False,bar_width=3)
        body=BoxLayout(orientation="vertical",padding=[dp(16),dp(12),dp(16),dp(24)],spacing=dp(11),size_hint_y=None); body.bind(minimum_height=body.setter("height"))
        head=BoxLayout(size_hint_y=None,height=dp(48),spacing=dp(6)); head.add_widget(text("Weather",23,WHITE,True))
        head.add_widget(self.btn("Vault",self.open_vault,72,CARD)); head.add_widget(self.btn("°C",lambda:self.set_units("metric"),45,CARD)); head.add_widget(self.btn("°F",lambda:self.set_units("imperial"),45,CARD)); body.add_widget(head)
        search=BoxLayout(size_hint_y=None,height=dp(49),spacing=dp(6)); self.query=TextInput(hint_text="Search city or region…",multiline=False,font_size=sp(15),foreground_color=WHITE,hint_text_color=MUTED,cursor_color=BLUE,background_normal="",background_active="",background_color=CARD2,padding=[dp(12),dp(12),dp(6),dp(8)])
        self.query.bind(text=self.on_query,on_text_validate=lambda *_:self.submit()); search.add_widget(self.query); search.add_widget(self.btn("⌖",self.current_location,48,(.16,.38,.62,1),20)); search.add_widget(self.btn("Go",self.submit,50,(.2,.43,.72,1))); body.add_widget(search)
        self.status=text("Search a city or use current location",11,MUTED,h=24); body.add_widget(self.status)
        self.suggestion_box=BoxLayout(orientation="vertical",spacing=dp(4),size_hint_y=None,height=0); body.add_widget(self.suggestion_box)
        self.hero=Card(orientation="vertical",padding=dp(15),spacing=dp(3),size_hint_y=None,height=dp(260))
        self.city=text("Your weather",22,WHITE,True,34); self.region=text("Choose a city to begin",11,MUTED,h=22); self.hero.add_widget(self.city); self.hero.add_widget(self.region)
        mid=BoxLayout(); self.art=Art(size_hint_x=.48); mid.add_widget(self.art); right=BoxLayout(orientation="vertical"); self.temp=text("--°",55,WHITE,True,68); self.cond=text("Ready when you are",16,WHITE,True,28); self.ext=text("Feels --°  ·  H --°  L --°",11,MUTED,h=25)
        right.add_widget(self.temp);right.add_widget(self.cond);right.add_widget(self.ext);mid.add_widget(right);self.hero.add_widget(mid);self.sun=text("Sunrise --   ·   Sunset --",11,MUTED,h=24,align="center");self.hero.add_widget(self.sun);body.add_widget(self.hero)
        self.hour_card=Card(orientation="vertical",padding=dp(13),spacing=dp(4),size_hint_y=None,height=dp(196));self.hour_card.add_widget(text("NEXT HOURS  ·  TEMP & RAIN CHANCE",10,MUTED,True,21));self.trend=Trend(size_hint_y=None,height=dp(54));self.hour_card.add_widget(self.trend)
        hs=ScrollView(do_scroll_y=False,size_hint_y=None,height=dp(91),bar_width=2);self.hours=BoxLayout(spacing=dp(8),size_hint_x=None);self.hours.bind(minimum_width=self.hours.setter("width"));hs.add_widget(self.hours);self.hour_card.add_widget(hs);body.add_widget(self.hour_card)
        self.stats=GridLayout(cols=2,spacing=dp(8),size_hint_y=None,height=dp(180));self.stat={}
        for key,title in (("feels","FEELS LIKE"),("humidity","HUMIDITY"),("wind","WIND"),("uv","UV INDEX")):
            tile=Card(orientation="vertical",padding=dp(10),spacing=dp(1),card_color=CARD2);tile.add_widget(text(title,9,MUTED,True,18));v=text("—",21,WHITE,True,30);sub=text("Awaiting forecast",10,MUTED,h=21);tile.add_widget(v);tile.add_widget(sub);self.stat[key]=(v,sub);self.stats.add_widget(tile)
        body.add_widget(self.stats);self.week=Card(orientation="vertical",padding=dp(13),spacing=dp(3),size_hint_y=None);self.week.add_widget(text("7-DAY OUTLOOK",10,MUTED,True,21));self.days=BoxLayout(orientation="vertical",size_hint_y=None,height=dp(252));self.week.add_widget(self.days);body.add_widget(self.week)
        body.add_widget(text("Open-Meteo forecasts · GPS when available",9,MUTED,h=22,align="center"));scroll.add_widget(body);self.root_box.add_widget(scroll)
    def setup_code(self):
        pop=ModalView(size_hint=(.92,None),height=dp(410),auto_dismiss=False,background_color=(0,0,0,.65));box=Card(orientation="vertical",padding=dp(19),spacing=dp(10),card_color=(.07,.13,.24,1))
        box.add_widget(text("Create your vault code",21,WHITE,True,38,"center"));box.add_widget(text("Starts with a number or symbol so it is never sent as a city search.",11,MUTED,h=44,align="center"))
        a=TextInput(hint_text="Code (6+ characters)",password=True,multiline=False,size_hint_y=None,height=dp(45));b=TextInput(hint_text="Confirm code",password=True,multiline=False,size_hint_y=None,height=dp(45));box.add_widget(a);box.add_widget(b);msg=text("",10,(1,.5,.5,1),h=25,align="center");box.add_widget(msg)
        def save():
            if len(a.text)<6 or a.text!=b.text or a.text[0].isalpha() or a.text!=a.text.strip():msg.text="Use matching 6+ character codes starting with a symbol or number.";return
            self.store.setup(a.text);pop.dismiss()
        box.add_widget(self.btn("Create code",save,color=(.18,.43,.75,1)));pop.add_widget(box);pop.open()
    def on_query(self,widget,value):
        if self.suggest_event:self.suggest_event.cancel()
        q=value.strip()
        if len(q)<2 or not q[0].isalpha():self.clear_suggestions();return
        self.suggest_event=Clock.schedule_once(lambda dt:self.suggestions(q),.4)
    def clear_suggestions(self):self.suggestion_box.clear_widgets();self.suggestion_box.height=0
    def suggestions(self,q):
        self.suggest_seq+=1; seq=self.suggest_seq
        def work():
            try: places=search_places(q,6)
            except Exception: places=[]
            Clock.schedule_once(lambda dt:self.show_suggestions(q,places) if seq==self.suggest_seq else None,0)
        threading.Thread(target=work,daemon=True).start()
    def show_suggestions(self,q,places):
        if self.query.text.strip()!=q:return
        self.clear_suggestions()
        for p in places:
            self.suggestion_box.add_widget(self.btn(p.label,lambda p=p:self.choose(p),color=(.10,.18,.31,1),size=11))
        if places:self.suggestion_box.height=dp(46*len(places))
    def choose(self,p):self.query.text=p.name;self.clear_suggestions();self.load(p)
    def submit(self):
        q=self.query.text.strip();self.clear_suggestions()
        if not q:self.status.text="Enter a city name first.";return
        if not q[0].isalpha():
            if not self.store.config.exists():self.setup_code()
            elif self.store.unlock(q):self.show_vault()
            else:self.status.text="Location not found." 
            return
        self.status.text="Finding city…"
        def work():
            places=search_places(q,1)
            if not places:raise RuntimeError("No matching city found. Try adding a region or country.")
            return places[0],forecast(places[0],self.units)
        self.net(work,self.render)
    def load(self,p,silent=False):
        self.status.text="Refreshing forecast…";self.net(lambda:(p,forecast(p,self.units)),self.render,silent)
    def net(self,work,done,silent=False):
        self.req_seq+=1;seq=self.req_seq
        def run():
            try:r=work();err=None
            except Exception as e:r=None;err=str(e)
            def finish(dt):
                if seq!=self.req_seq:return
                if err:
                    if not silent:self.status.text=err
                else:done(r)
            Clock.schedule_once(finish,0)
        threading.Thread(target=run,daemon=True).start()
    def render(self,result):
        p,d=result;self.current_place,self.current_data=p,d;self.art.kind=condition(d["code"])[1];self.art.day=int(d["day"])
        self.city.text=p.name;self.region.text=" · ".join(x for x in (p.admin,p.country) if x)+"  ·  "+datetime.fromisoformat(d["time"]).strftime("%a %b %d, %I:%M %p")
        self.temp.text=f"{round(d['temp'])}°";self.cond.text=condition(d["code"])[0];self.ext.text=f"Feels {round(d['feels'])}°   ·   H {round(d['days'][0]['high'])}°   L {round(d['days'][0]['low'])}°"
        self.sun.text=f"☼  {self.clock(d['sunrise'])}     ☾  {self.clock(d['sunset'])}";self.status.text="Live forecast · updated "+datetime.now().strftime("%I:%M %p").lstrip("0")
        self.trend.values=[h["temp"] for h in d["hours"]];self.hours.clear_widgets()
        for i,h in enumerate(d["hours"]):
            tile=BoxLayout(orientation="vertical",size_hint=(None,1),width=54,spacing=dp(1));tile.add_widget(text("Now" if i==0 else datetime.fromisoformat(h["time"]).strftime("%I %p").lstrip("0"),9,MUTED,h=17,align="center"));tile.add_widget(text(emoji(condition(h["code"])[1],h["day"]),20,WHITE,True,h=25,align="center"));tile.add_widget(text(f"{round(h['temp'])}°",13,WHITE,True,h=20,align="center"));rain="–" if h["rain"] is None else f"{h['rain']}%";tile.add_widget(text(rain,9,(.55,.78,1,1) if h["rain"] and h["rain"]>=20 else MUTED,h=14,align="center"));self.hours.add_widget(tile)
        self.days.clear_widgets();lo=min(x["low"] for x in d["days"]);hi=max(x["high"] for x in d["days"])
        for i,day in enumerate(d["days"]):
            row=BoxLayout(size_hint_y=None,height=dp(35),spacing=dp(4));name="Today" if i==0 else datetime.strptime(day["date"],"%Y-%m-%d").strftime("%a")
            row.add_widget(text(name,11,WHITE,True,h=26));row.add_widget(text(emoji(condition(day["code"])[1]),17,WHITE,h=26,align="center"));row.add_widget(text(f"{round(day['low'])}°",11,MUTED,h=26,align="right"))
            bar=Range(size_hint_x=.95,height=dp(22),low=day["low"],high=day["high"],minimum=lo,maximum=hi);row.add_widget(bar);row.add_widget(text(f"{round(day['high'])}°",11,WHITE,True,h=26));self.days.add_widget(row)
        try:
            with open(self.last_file,"w",encoding="utf8") as f:json.dump(p.__dict__,f)
        except OSError:pass
    @staticmethod
    def clock(v):return datetime.fromisoformat(v).strftime("%I:%M %p").lstrip("0")
    def restore_last(self):
        try:
            with open(self.last_file,encoding="utf8") as f:self.load(Place(**json.load(f)),True)
        except (OSError,ValueError,TypeError):pass
    def set_units(self,value):
        if self.units!=value:self.units=value;self.load(self.current_place) if self.current_place else None
    def current_location(self):
        self.status.text="Requesting location…"
        try:
            from android.permissions import Permission,request_permissions
            def granted(perms,results):
                if not results or not any(results):Clock.schedule_once(lambda dt:self.ip_location(),0);return
                try:
                    from plyer import gps
                    self.gps_received=False
                    gps.configure(on_location=self.gps_location,on_status=lambda *a:None);gps.start(minTime=1000,minDistance=10)
                    Clock.schedule_once(lambda dt:self.ip_location() if not self.gps_received else None,12)
                except Exception:Clock.schedule_once(lambda dt:self.ip_location(),0)
            request_permissions([Permission.ACCESS_FINE_LOCATION,Permission.ACCESS_COARSE_LOCATION],granted)
        except ImportError:self.ip_location()
    def gps_location(self,**kw):
        self.gps_received=True
        try:
            from plyer import gps;gps.stop()
        except Exception:pass
        self.load(Place("Current location","","",float(kw["lat"]),float(kw["lon"])))
    def ip_location(self):
        self.status.text="Using approximate network location…"
        def work():
            d=get_json("https://ipapi.co/json/");p=Place(d.get("city") or "Nearby",d.get("region") or "",d.get("country_name") or "",float(d["latitude"]),float(d["longitude"]))
            return p,forecast(p,self.units)
        self.net(work,self.render)
    def open_vault(self):
        if not self.store.config.exists():self.setup_code();return
        if not self.store.key:
            if not self.store.unlock(self.query.text.strip()):self.status.text="Type your vault code into search and press Go.";return
        self.show_vault()
    def show_vault(self):
        view=ModalView(size_hint=(.96,.92),background_color=(.01,.025,.06,.93));box=Card(orientation="vertical",padding=dp(12),spacing=dp(7),card_color=(.045,.07,.12,1))
        head=BoxLayout(size_hint_y=None,height=dp(43));head.add_widget(text("Private vault",20,WHITE,True));head.add_widget(self.btn("Lock",lambda:(setattr(self.store,"key",None),view.dismiss()),65,(.48,.16,.2,1)));box.add_widget(head)
        box.add_widget(text("Files are copied into app storage. The vault index is encrypted.",10,MUTED,h=23))
        actions=BoxLayout(size_hint_y=None,height=dp(42),spacing=dp(4))
        for name,fn in (("Import file",self.pick_file),("Folder ZIP",self.pick_folder),("New file",lambda:self.create_item("File")),("New folder",lambda:self.create_item("Folder"))):actions.add_widget(self.btn(name,fn,color=(.15,.32,.52,1),size=10))
        box.add_widget(actions);scroll=ScrollView();self.vault_rows=BoxLayout(orientation="vertical",size_hint_y=None,spacing=dp(4));self.vault_rows.bind(minimum_height=self.vault_rows.setter("height"));scroll.add_widget(self.vault_rows);box.add_widget(scroll)
        self.vault_status=text("",10,MUTED,h=23);box.add_widget(self.vault_status);self.vault_view=view;self.refresh_vault();view.add_widget(box);self.pause_animations();view.bind(on_dismiss=lambda *_:self.resume_animations());view.open()
    def pause_animations(self):
        for widget in (getattr(self,"art",None),getattr(self,"trend",None)):
            event=getattr(widget,"_event",None)
            if event:event.cancel();widget._event=None
    def resume_animations(self):
        if getattr(self,"art",None) and not self.art._event:self.art._event=Clock.schedule_interval(self.art.tick,1/12)
        if getattr(self,"trend",None) and not self.trend._event:self.trend._event=Clock.schedule_interval(self.trend.advance,.3)
    def refresh_vault(self):
        if not hasattr(self,"vault_rows"):return
        self.vault_rows.clear_widgets()
        for e in self.store.entries:
            row=BoxLayout(size_hint_y=None,height=dp(44),spacing=dp(4));row.add_widget(text(("▰ " if e["kind"]=="Folder" else "▤ ")+e["name"],11,WHITE,h=40));row.add_widget(self.btn("Open",lambda e=e:self.open_saved(e),54,(.15,.27,.43,1),10));row.add_widget(self.btn("×",lambda e=e:self.remove_saved(e),38,(.4,.14,.19,1),15));self.vault_rows.add_widget(row)
        if not self.store.entries:self.vault_rows.add_widget(text("No saved items yet.",12,MUTED,h=48,align="center"))
    def pick_file(self):
        try:
            from androidstorage4kivy import Chooser
            if not hasattr(self,"chooser"):self.chooser=Chooser(self.shared_selection)
            self._import_as_folder=False
            self.chooser.choose_content("*/*")
        except Exception as e:self.vault_status.text=f"File picker unavailable: {e}"
    def pick_folder(self):
        try:
            from androidstorage4kivy import Chooser
            if not hasattr(self,"chooser"):self.chooser=Chooser(self.shared_selection)
            self._import_as_folder=True
            self.chooser.choose_content("*/*")
            self.vault_status.text="Choose a ZIP archive containing the folder."
        except Exception:self.vault_status.text="Folder import uses ZIP archives. You can also create a folder here."
    def shared_selection(self,selection):
        if not selection:return
        self.vault_status.text="Copying item into saved files…"
        def work():
            import os
            try:
                from androidstorage4kivy import SharedStorage
                path=SharedStorage().copy_from_shared(selection[0])
                if not path:raise RuntimeError("Android couldn't read that item.")
                if self._import_as_folder:self.store.add_archive(path);msg="Folder copied into saved files."
                else:self.store.add_copy(path);msg="File copied into saved files."
                try:os.remove(path)
                except OSError:pass
            except Exception as e:msg=f"Copy failed: {e}"
            Clock.schedule_once(lambda dt:self.copy_done(msg),0)
        threading.Thread(target=work,daemon=True).start()
    def copy_done(self,msg):self.vault_status.text=msg;self.refresh_vault()
    def create_item(self,kind):
        pop=ModalView(size_hint=(.9,None),height=dp(210),background_color=(0,0,0,.65));box=Card(orientation="vertical",padding=dp(16),spacing=dp(8),card_color=(.07,.12,.2,1));box.add_widget(text(f"Create {kind.lower()}",17,WHITE,True,30))
        entry=TextInput(hint_text=f"Name for {kind.lower()}",multiline=False,size_hint_y=None,height=dp(44));box.add_widget(entry);msg=text("",10,(1,.5,.5,1),h=22);box.add_widget(msg)
        def save():
            n=entry.text.strip()
            if not n or n in (".","..") or "/" in n or "\\" in n:msg.text="Use a simple name without slashes.";return
            try:self.store.create(n,kind);pop.dismiss();self.refresh_vault();self.vault_status.text=f"Created {n} in saved files."
            except Exception as e:msg.text=str(e)
        box.add_widget(self.btn("Create",save,color=(.18,.43,.75,1)));pop.add_widget(box);pop.open()
    def open_saved(self,e):
        if e["kind"]=="Folder":
            import os
            pop=ModalView(size_hint=(.88,.7),background_color=(0,0,0,.75));box=Card(orientation="vertical",padding=dp(14),spacing=dp(6),card_color=(.07,.12,.2,1));box.add_widget(text(e["name"],17,WHITE,True,30))
            sc=ScrollView();items=BoxLayout(orientation="vertical",size_hint_y=None);items.bind(minimum_height=items.setter("height"))
            for name in os.listdir(e["path"]):items.add_widget(text("▰  "+name,12,MUTED,h=34))
            sc.add_widget(items);box.add_widget(sc);box.add_widget(self.btn("Done",pop.dismiss,color=CARD2));pop.add_widget(box);pop.open();return
        try:
            from androidstorage4kivy import SharedStorage,ShareSheet
            shared=SharedStorage().copy_to_shared(e["path"])
            if shared:
                if not hasattr(self,"share_sheet"):self.share_sheet=ShareSheet()
                self.share_sheet.view_file(shared)
            else:self.vault_status.text="Could not open this file."
        except Exception:self.vault_status.text="This copy is stored in app-private files."
    def remove_saved(self,e):self.store.remove([e["id"]]);self.refresh_vault();self.vault_status.text=f"Removed {e['name']} and its app copy."

if __name__=="__main__":WeatherApp().run()
