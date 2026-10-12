from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.section import WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_BREAK
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_ROW_HEIGHT_RULE
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import textwrap

ROOT = Path(r"E:\Code\AIProgect\travel assistant")
OUT = ROOT / "旅游规划助手_PRD_v1.2.docx"
ASSETS = ROOT / "prd_assets"
ASSETS.mkdir(exist_ok=True)


def box(ax, x, y, w, h, title, sub='', fc='#EAF2F8', ec='#2F5597'):
    p = FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.015,rounding_size=0.02',fc=fc,ec=ec,lw=1.5)
    ax.add_patch(p)
    ax.text(x+w/2,y+h*0.62,title,ha='center',va='center',fontsize=10,fontweight='bold',color='#17202A')
    if sub: ax.text(x+w/2,y+h*0.28,sub,ha='center',va='center',fontsize=7.5,color='#34495E',wrap=True)
    return p

def arrow(ax, a, b, color='#566573'):
    ax.add_patch(FancyArrowPatch(a,b,arrowstyle='-|>',mutation_scale=12,lw=1.2,color=color))

def architecture_png():
    fig, ax = plt.subplots(figsize=(12,7.2)); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
    ax.add_patch(FancyBboxPatch((.02,.04),.96,.90,boxstyle='round,pad=.015',fc='#FAFAFA',ec='#7F8C8D',lw=1.4,ls='--'))
    ax.text(.04,.91,'Docker Compose 运行边界',fontsize=11,fontweight='bold')
    box(ax,.06,.72,.18,.12,'Chainlit Web','对话、步骤、引用、反馈')
    box(ax,.31,.72,.18,.12,'FastAPI','SSE / REST / 健康检查')
    box(ax,.57,.72,.18,.12,'LangGraph Agent','意图路由、状态机、工具编排')
    box(ax,.79,.72,.15,.12,'SQLite','会话检查点 / 审计')
    arrow(ax,(.24,.78),(.31,.78)); arrow(ax,(.49,.78),(.57,.78)); arrow(ax,(.75,.78),(.79,.78))
    box(ax,.08,.46,.18,.13,'结构化理解','Pydantic 槽位与校验','#FDEBD0','#B9770E')
    box(ax,.31,.46,.18,.13,'混合检索','ChromaDB + BM25 + RRF','#E8F8F5','#148F77')
    box(ax,.54,.46,.18,.13,'旅行工具层','天气 / POI / 路线 / 酒店 / 交通','#FDEDEC','#B03A2E')
    box(ax,.77,.46,.17,.13,'答案合成','引用、约束检查、流式输出','#F4ECF7','#7D3C98')
    for x in [.17,.40,.63,.855]: arrow(ax,(.655,.72),(x,.59))
    arrow(ax,(.26,.525),(.31,.525)); arrow(ax,(.49,.525),(.54,.525)); arrow(ax,(.72,.525),(.77,.525))
    box(ax,.09,.18,.18,.13,'DeepSeek API','意图识别与生成；限流/超时/重试','#EBF5FB','#2874A6')
    box(ax,.32,.18,.18,.13,'BGE Embedding API','失败时可切换本地小模型','#EBF5FB','#2874A6')
    box(ax,.55,.18,.18,.13,'公开数据 API','Open-Meteo / OpenTripMap / 路由','#EBF5FB','#2874A6')
    box(ax,.78,.18,.15,.13,'知识数据','Wikivoyage / GeoNames / 自建攻略','#EBF5FB','#2874A6')
    arrow(ax,(.17,.46),(.18,.31)); arrow(ax,(.40,.46),(.41,.31)); arrow(ax,(.63,.46),(.64,.31)); arrow(ax,(.40,.46),(.855,.31))
    ax.text(.5,.07,'设计原则：单智能体状态图 + 可插拔适配器；全部业务数据来自正式授权或公开真实 API',ha='center',fontsize=9,color='#424949')
    fig.tight_layout(); p=ASSETS/'architecture.png'; fig.savefig(p,dpi=220,bbox_inches='tight'); plt.close(fig); return p

def flow_png():
    fig, ax = plt.subplots(figsize=(11,5.5)); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
    nodes=[(.04,.68,.15,.14,'用户输入','自然语言'),(.24,.68,.15,.14,'意图与槽位','一次 LLM 调用'),(.44,.68,.15,.14,'参数校验','缺失则追问'),(.64,.68,.15,.14,'Agent 路由','RAG / 工具 / 闲聊'),(.82,.68,.14,.14,'并行执行','检索与 API')]
    for n in nodes: box(ax,*n)
    for i in range(len(nodes)-1): arrow(ax,(nodes[i][0]+nodes[i][2],.75),(nodes[i+1][0],.75))
    box(ax,.70,.30,.18,.14,'证据与约束校验','引用、预算、时间、距离','#E8F8F5','#148F77')
    box(ax,.43,.30,.18,.14,'答案合成','行程表 + 来源 + 风险提示','#F4ECF7','#7D3C98')
    box(ax,.16,.30,.18,.14,'SSE 流式返回','展示工具步骤与引用','#FDEBD0','#B9770E')
    arrow(ax,(.89,.68),(.79,.44)); arrow(ax,(.70,.37),(.61,.37)); arrow(ax,(.43,.37),(.34,.37))
    arrow(ax,(.25,.30),(.25,.17)); arrow(ax,(.25,.17),(.515,.17)); arrow(ax,(.515,.17),(.515,.30))
    ax.text(.38,.12,'用户修订或继续追问：使用 thread_id 从 SQLite 检查点恢复',fontsize=9)
    fig.tight_layout(); p=ASSETS/'agent_flow.png'; fig.savefig(p,dpi=220,bbox_inches='tight'); plt.close(fig); return p

def test_png():
    fig, ax = plt.subplots(figsize=(11,4.8)); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
    labels=[('提交 PR','触发 CI'),('静态门禁','Ruff / Mypy / Bandit'),('自动测试','Unit / API / RAG'),('镜像验证','Build / Trivy / 启动'),('评测报告','指标与失败样例'),('人工评审','批准后合并')]
    xs=[.02,.185,.35,.515,.68,.845]
    colors=['#EBF5FB','#E8F8F5','#FDEBD0','#FDEDEC','#F4ECF7','#EAF2F8']
    ecs=['#2874A6','#148F77','#B9770E','#B03A2E','#7D3C98','#2F5597']
    for i,(t,s) in enumerate(labels): box(ax,xs[i],.48,.135,.20,t,s,colors[i],ecs[i])
    for i in range(5): arrow(ax,(xs[i]+.135,.58),(xs[i+1],.58))
    ax.text(.5,.26,'任一必需检查失败 → 阻止合并 → 上传日志、覆盖率、RAG 评测与容器扫描报告',ha='center',fontsize=10,fontweight='bold')
    ax.text(.5,.13,'可选 AI Review：仅分析 diff，不执行代码；外部 Fork 不读取密钥；建议以评论形式输出，不作为唯一门禁',ha='center',fontsize=9,color='#555')
    fig.tight_layout(); p=ASSETS/'test_flow.png'; fig.savefig(p,dpi=220,bbox_inches='tight'); plt.close(fig); return p

def set_cell_shading(cell, fill):
    tcPr=cell._tc.get_or_add_tcPr(); shd=tcPr.find(qn('w:shd'))
    if shd is None: shd=OxmlElement('w:shd'); tcPr.append(shd)
    shd.set(qn('w:fill'),fill)

def set_cell_margins(cell, top=100, start=120, bottom=100, end=120):
    tc=cell._tc; tcPr=tc.get_or_add_tcPr(); tcMar=tcPr.first_child_found_in('w:tcMar')
    if tcMar is None: tcMar=OxmlElement('w:tcMar'); tcPr.append(tcMar)
    for m,v in [('top',top),('start',start),('bottom',bottom),('end',end)]:
        node=tcMar.find(qn('w:'+m))
        if node is None: node=OxmlElement('w:'+m); tcMar.append(node)
        node.set(qn('w:w'),str(v)); node.set(qn('w:type'),'dxa')

def set_repeat_table_header(row):
    trPr=row._tr.get_or_add_trPr(); tblHeader=OxmlElement('w:tblHeader'); tblHeader.set(qn('w:val'),'true'); trPr.append(tblHeader)

def add_hyperlink(paragraph, text, url):
    part=paragraph.part; rid=part.relate_to(url,'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',is_external=True)
    h=OxmlElement('w:hyperlink'); h.set(qn('r:id'),rid)
    r=OxmlElement('w:r'); rPr=OxmlElement('w:rPr'); color=OxmlElement('w:color'); color.set(qn('w:val'),'0563C1'); rPr.append(color)
    u=OxmlElement('w:u'); u.set(qn('w:val'),'single'); rPr.append(u); r.append(rPr)
    t=OxmlElement('w:t'); t.text=text; r.append(t); h.append(r); paragraph._p.append(h)

def table(doc, headers, rows, widths=None, font=8.5):
    t=doc.add_table(rows=1, cols=len(headers)); t.alignment=WD_TABLE_ALIGNMENT.CENTER; t.style='Table Grid'; t.autofit=False
    hdr=t.rows[0]; set_repeat_table_header(hdr)
    for i,h in enumerate(headers):
        c=hdr.cells[i]; c.text=str(h); set_cell_shading(c,'1F4E78'); c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER; set_cell_margins(c)
        for r in c.paragraphs[0].runs: r.font.bold=True; r.font.color.rgb=RGBColor(255,255,255); r.font.size=Pt(font)
        c.paragraphs[0].alignment=WD_ALIGN_PARAGRAPH.CENTER
        if widths: c.width=Cm(widths[i])
    for ri,row in enumerate(rows):
        cells=t.add_row().cells
        for i,val in enumerate(row):
            cells[i].text=str(val); cells[i].vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER; set_cell_margins(cells[i])
            if ri%2: set_cell_shading(cells[i],'F3F6F9')
            if widths: cells[i].width=Cm(widths[i])
            for p in cells[i].paragraphs:
                p.paragraph_format.space_after=Pt(0); p.paragraph_format.line_spacing=1.05
                for r in p.runs: r.font.size=Pt(font); r.font.name='Microsoft YaHei'; r._element.rPr.rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
        t.rows[-1].height_rule=WD_ROW_HEIGHT_RULE.AT_LEAST
    doc.add_paragraph().paragraph_format.space_after=Pt(0)
    return t

def bullet(doc, text, level=0):
    p=doc.add_paragraph(style='List Bullet' if level==0 else 'List Bullet 2'); p.add_run(text); return p

_num_counter = 0
def reset_number():
    global _num_counter
    _num_counter = 0
def number(doc, text):
    global _num_counter
    _num_counter += 1
    p=doc.add_paragraph(); p.paragraph_format.left_indent=Cm(0.55); p.paragraph_format.first_line_indent=Cm(-0.55)
    p.add_run(f'{_num_counter}.  ' + text); return p

def caption(doc,text):
    p=doc.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER; r=p.add_run(text); r.bold=True; r.font.size=Pt(9); p.paragraph_format.space_after=Pt(6)

# PIL-based diagrams keep the build independent of optional plotting libraries.
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
def _font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
def _rounded(draw, xy, title, sub='', fill='#EAF2F8', outline='#2F5597'):
    draw.rounded_rectangle(xy, radius=14, fill=fill, outline=outline, width=3)
    x1,y1,x2,y2=xy; cx=(x1+x2)//2
    bb=draw.textbbox((0,0),title,font=_font(24,True)); draw.text((cx-(bb[2]-bb[0])/2,y1+18),title,font=_font(24,True),fill='#17202A')
    if sub:
        lines=textwrap.wrap(sub,width=max(10,int((x2-x1)/18)))
        yy=y1+58
        for line in lines[:2]:
            bb=draw.textbbox((0,0),line,font=_font(17)); draw.text((cx-(bb[2]-bb[0])/2,yy),line,font=_font(17),fill='#34495E'); yy+=24
def _arr(draw,a,b):
    draw.line([a,b],fill='#566573',width=4)
    import math
    ang=math.atan2(b[1]-a[1],b[0]-a[0]); L=14
    pts=[b,(b[0]-L*math.cos(ang-.55),b[1]-L*math.sin(ang-.55)),(b[0]-L*math.cos(ang+.55),b[1]-L*math.sin(ang+.55))]
    draw.polygon(pts,fill='#566573')
def architecture_png():
    im=Image.new('RGB',(2200,1320),'white'); d=ImageDraw.Draw(im)
    d.rounded_rectangle((35,35,2165,1250),radius=22,fill='#FAFAFA',outline='#7F8C8D',width=3)
    d.text((70,55),'Docker Compose 运行边界',font=_font(27,True),fill='black')
    top=[(110,180,480,340,'Chainlit Web','对话、步骤、引用、反馈'),(590,180,960,340,'FastAPI','SSE / REST / 健康检查'),(1070,180,1470,340,'LangGraph Agent','意图路由、状态机、工具编排'),(1600,180,2020,340,'SQLite','会话检查点 / 审计')]
    for x1,y1,x2,y2,t,s in top:_rounded(d,(x1,y1,x2,y2),t,s)
    for a,b in [((480,260),(590,260)),((960,260),(1070,260)),((1470,260),(1600,260))]:_arr(d,a,b)
    mid=[(80,510,480,690,'结构化理解','Pydantic 槽位与校验','#FDEBD0','#B9770E'),(590,510,990,690,'混合检索','ChromaDB + BM25 + RRF','#E8F8F5','#148F77'),(1100,510,1500,690,'旅行工具层','天气 / POI / 路线 / 酒店 / 交通','#FDEDEC','#B03A2E'),(1610,510,2030,690,'答案合成','引用、约束检查、流式输出','#F4ECF7','#7D3C98')]
    for x1,y1,x2,y2,t,s,f,e in mid:_rounded(d,(x1,y1,x2,y2),t,s,f,e)
    for a,b in [((1270,340),(280,510)),((1270,340),(790,510)),((1270,340),(1300,510)),((1270,340),(1820,510)),((480,600),(590,600)),((990,600),(1100,600)),((1500,600),(1610,600))]:_arr(d,a,b)
    bot=[(80,870,480,1050,'DeepSeek API','意图识别与生成；限流、超时、重试'),(590,870,990,1050,'BGE Embedding API','失败时可切换本地小模型'),(1100,870,1500,1050,'公开数据 API','Open-Meteo / POI / 路由'),(1610,870,2030,1050,'知识数据','Wikivoyage / GeoNames / 自建攻略')]
    for x1,y1,x2,y2,t,s in bot:_rounded(d,(x1,y1,x2,y2),t,s,'#EBF5FB','#2874A6')
    for a,b in [((280,690),(280,870)),((790,690),(790,870)),((1300,690),(1300,870)),((790,690),(1820,870))]:_arr(d,a,b)
    d.text((260,1140),'设计原则：单智能体状态图 + 可插拔适配器；全部业务数据来自正式授权或公开真实 API',font=_font(24),fill='#424949')
    p=ASSETS/'architecture.png'; im.save(p); return p
def flow_png():
    im=Image.new('RGB',(2200,980),'white'); d=ImageDraw.Draw(im)
    xs=[40,430,820,1210,1600]; labels=[('用户输入','自然语言'),('意图与槽位','一次 LLM 调用'),('参数校验','缺失则追问'),('Agent 路由','RAG / 工具 / 闲聊'),('并行执行','检索与 API')]
    for x,(t,s) in zip(xs,labels):_rounded(d,(x,150,x+320,320),t,s)
    for i in range(4):_arr(d,(xs[i]+320,235),(xs[i+1],235))
    lower=[(1450,520,1800,690,'证据与约束校验','引用、预算、时间、距离'),(930,520,1280,690,'答案合成','行程表 + 来源 + 风险提示'),(410,520,760,690,'SSE 流式返回','展示工具步骤与引用')]
    for x1,y1,x2,y2,t,s in lower:_rounded(d,(x1,y1,x2,y2),t,s,'#E8F8F5','#148F77')
    for a,b in [((1760,320),(1625,520)),((1450,605),(1280,605)),((930,605),(760,605))]:_arr(d,a,b)
    d.text((500,810),'用户修订或继续追问：使用 thread_id 从 SQLite 检查点恢复',font=_font(27,True),fill='#333333')
    p=ASSETS/'agent_flow.png'; im.save(p); return p
def test_png():
    im=Image.new('RGB',(2200,760),'white'); d=ImageDraw.Draw(im)
    xs=[30,390,750,1110,1470,1830]; labels=[('提交 PR','触发 CI'),('静态门禁','Ruff / Mypy / Bandit'),('自动测试','Unit / API / RAG'),('镜像验证','Build / Trivy / 启动'),('评测报告','指标与失败样例'),('人工评审','批准后合并')]
    for x,(t,s) in zip(xs,labels):_rounded(d,(x,150,x+290,330),t,s)
    for i in range(5):_arr(d,(xs[i]+290,240),(xs[i+1],240))
    d.text((190,470),'任一必需检查失败 → 阻止合并 → 上传日志、覆盖率、RAG 评测与容器扫描报告',font=_font(29,True),fill='#222222')
    d.text((180,570),'可选 AI Review：仅分析 diff，不执行代码；外部 Fork 不读取密钥；建议以评论形式输出，不作为唯一门禁',font=_font(24),fill='#555555')
    p=ASSETS/'test_flow.png'; im.save(p); return p

arch=architecture_png(); flow=flow_png(); testflow=test_png()
doc=Document()
sec=doc.sections[0]; sec.top_margin=Cm(2.2); sec.bottom_margin=Cm(2.0); sec.left_margin=Cm(2.2); sec.right_margin=Cm(2.2)
styles=doc.styles
normal=styles['Normal']; normal.font.name='Microsoft YaHei'; normal._element.rPr.rFonts.set(qn('w:eastAsia'),'Microsoft YaHei'); normal.font.size=Pt(10.5)
normal.paragraph_format.line_spacing=1.35; normal.paragraph_format.space_after=Pt(6)
for name,size in [('Title',24),('Heading 1',16),('Heading 2',13),('Heading 3',11)]:
    s=styles[name]; s.font.name='Microsoft YaHei'; s._element.rPr.rFonts.set(qn('w:eastAsia'),'Microsoft YaHei'); s.font.size=Pt(size); s.font.color.rgb=RGBColor(0,0,0); s.font.bold=True
    s.paragraph_format.space_before=Pt(12); s.paragraph_format.space_after=Pt(6); s.paragraph_format.keep_with_next=True
styles['Title'].paragraph_format.space_before=Pt(0)
title_ppr=styles['Title']._element.get_or_add_pPr()
title_border=title_ppr.find(qn('w:pBdr'))
if title_border is not None:
    title_ppr.remove(title_border)

p=doc.add_paragraph(style='Title'); p.alignment=WD_ALIGN_PARAGRAPH.CENTER; p.add_run('旅游规划助手产品需求文档')
p=doc.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER; r=p.add_run('面向人工智能创新开发实践课程设计的轻量级智能体系统'); r.bold=True; r.font.size=Pt(13)
doc.add_paragraph()
table(doc,['字段','内容'],[
    ['文档版本','v1.2'],['状态','已确认真实 API、自有主机部署方案与私有知识口径'],['创建日期','2026年10月11日'],['目标交付','部署到自有主机的 Docker Compose 旅游规划助手']],[4,12],9.5)
p=doc.add_paragraph(); p.add_run('结论摘要：').bold=True; p.add_run('本项目采用单个 LangGraph 状态图而非重型多智能体集群，以 DeepSeek API、Travelport 正式旅行 API、公开地理与天气 API、ChromaDB 和 SQLite 构建可演示、可测试、可追溯的旅游规划助手。所有业务数据均来自正式授权或公开真实 API，系统不提供模拟运行模式。')
p=doc.add_paragraph(); p.add_run('验收主线：').bold=True; p.add_run('七类意图可用；覆盖中国全境知识库；RAG Top3 命中率不低于 95%；有来源回答依据覆盖率达到 100%（知识证据与工具证据均绑定）；首 token P95 不超过 2 秒；Docker Compose 可在自有主机启动；GitHub Actions 在合并前完成质量、安全、测试和镜像门禁。')
p=doc.add_paragraph(); p.add_run('变更记录：').bold=True; p.add_run('v1.2（本次）新增 6.4 私有知识与证据口径，第 8 章补充“证据绑定率”与“无关知识引入”指标，5.4 的 SourceChunk 增加 source_type，第 15 章补充对应检查项。v1.1 确认真实 API 与自有主机部署方案。v1.0 首版。')
doc.add_page_break()

doc.add_heading('1 产品概述',level=1)
doc.add_heading('1.1 背景与问题',level=2)
doc.add_paragraph('用户在规划旅行时需要在攻略、天气、景点、住宿、交通和预算之间反复切换。普通聊天模型能生成流畅文本，但容易编造营业时间、票价或路线，也无法证明推荐来自哪里。本系统将自然语言交互、RAG 私有知识库与可验证的外部工具调用组合成一条轻量工作流，为课程演示提供完整的 Agent 开发闭环。')
doc.add_heading('1.2 产品目标',level=2)
table(doc,['目标','量化结果','验证方式'],[
 ['完成核心旅行任务','覆盖目的地、行程、交通、住宿、美食、天气、闲聊 7 类意图','固定测试集逐类执行'],
 ['提高答案可信度','RAG 回答给出来源；依据覆盖率 100%；Top3 命中率 ≥95%；无关知识引入 0 条','标注问答集 + 自动评测'],
 ['保持轻量部署','核心服务 4 个以内；无 GPU 也可运行；一条 Compose 命令启动','全新主机冷启动演练'],
 ['体现 Agent 能力','系统自主决定追问、检索、调用工具或直接回答','轨迹日志和 Chainlit 步骤展示'],
 ['形成工程闭环','PR 必须通过代码质量、测试、安全扫描、镜像构建','GitHub 分支保护与 Actions']],[4.0,7.1,5.3])
doc.add_heading('1.3 非目标与边界',level=2)
bullet(doc,'不直接完成机票、火车票、酒店的支付和下单；仅展示正式 API 返回的实时查询结果和供应商跳转链接。')
bullet(doc,'不保证第三方价格、余票、营业时间实时正确；答案必须显示数据时间与核验提示。')
bullet(doc,'首版不做复杂用户画像、社交、商家后台、地图导航和移动端原生应用。')
bullet(doc,'首版以中文交互和中国国内旅行场景为主，海外目的地为扩展项。')
doc.add_heading('1.4 用户与核心场景',level=2)
table(doc,['用户','核心任务','成功标准'],[
 ['学生演示者','用自然语言展示 Agent 路由、RAG、工具调用和多轮记忆','5分钟内完成从需求到行程的完整演示'],
 ['旅行计划者','输入目的地、天数、人数、偏好和预算，获得可执行计划','行程结构完整且约束冲突可解释'],
 ['课程教师','检查知识库、指标、工程规范、测试和文档','指标可复现，过程材料可追溯'],
 ['开发成员','本地开发、调试、提交 PR、部署','配置测试环境 API Key 后可执行真实接口测试']],[3.0,8.0,5.4])

doc.add_heading('2 需求依据与假设',level=1)
doc.add_heading('2.1 任务书硬性要求映射',level=2)
table(doc,['任务书要求','PRD 落地项','验收证据'],[
 ['理解自然语言并自主调用工具或检索知识库','LangGraph 条件路由；结构化意图与槽位','Agent 轨迹、单元测试、演示录像'],
 ['工具调用、RAG、知识库构建','工具适配层；ChromaDB；混合检索','索引脚本、检索评测、引用展示'],
 ['多轮对话与上下文','SQLite LangGraph Checkpointer；thread_id','连续 10 轮回归用例'],
 ['完整源码与部署文档','Dockerfile、compose.yaml、.env.example、README','全新环境部署记录'],
 ['AI 辅助开发档案','prompts、AI 原始输出、bug 日志、人工修正记录','docs/ai-dev-log 目录'],
 ['系统测试报告','功能、RAG、性能、故障、安全测试','CI 产出的 HTML/JSON 报告'],
 ['团队协作与例会','里程碑、任务分工、每周会议纪要模板','GitHub Projects 与 docs/meetings']],[4.4,7.4,4.6])
doc.add_heading('2.2 已采用假设',level=2)
bullet(doc,'课程答辩与部署均使用真实 API；每种外部工具必须有缓存、超时、熔断和错误提示，但不得用虚构数据替代失败结果。')
bullet(doc,'DeepSeek 使用 OpenAI 兼容 API；模型名称、Base URL 和密钥均通过环境变量配置，不写入代码或镜像。')
bullet(doc,'航班、酒店和铁路使用已申请正式凭据的 Travelport Universal API 或等价正式授权供应商 API；不抓取受限网站。')
bullet(doc,'知识库覆盖中国全部省级行政区、地级市和主要县级目的地；索引按省份与城市增量构建。')
bullet(doc,'性能指标在预热索引、并发 1、稳定网络和 20 条标准查询下测量；2 秒指标指首 token P95，同时单独报告完整响应和第三方 API 耗时。')

doc.add_heading('3 功能需求',level=1)
doc.add_heading('3.1 意图与槽位',level=2)
table(doc,['ID','意图','示例','工具与知识','必填槽位'],[
 ['INT-01','目的地信息查询','成都有什么好玩的','search_attractions + RAG','destination'],
 ['INT-02','行程规划','规划3天西安行程','create_itinerary + get_weather + RAG','destination, days'],
 ['INT-03','交通查询','广州到长沙高铁有哪些','search_trains / search_flights','start_city, target_city, date'],
 ['INT-04','住宿查询','西湖附近推荐酒店','search_hotels + RAG','city；可选 budget'],
 ['INT-05','美食推荐','长沙有什么必吃','search_food + RAG','city'],
 ['INT-06','天气查询','明天长沙天气怎样','get_weather','city, date'],
 ['INT-07','闲聊与兜底','你好；谢谢','无；边界提示','无']],[1.5,2.5,4.0,5.6,3.0],8)
doc.add_paragraph('统一槽位模型还包括 people_number、preferences、budget、start_date、end_date、transport_preference。槽位为空或歧义时，Agent 只能追问所需最少信息，不得猜测日期、人数或预算。')
doc.add_heading('3.2 需求明细与验收标准',level=2)
reqs=[
('FR-01','意图识别与结构化槽位','一次 LLM 调用输出受 Pydantic 约束的 JSON；解析失败自动修复一次，再失败进入澄清。','标注集宏平均准确率 ≥90%；必填槽位抽取 F1 ≥90%。'),
('FR-02','目的地检索','按城市、偏好、开放状态检索景点，展示名称、简介、位置、来源与抓取时间。','Top5 至少 3 条与查询相关；来源链接可打开。'),
('FR-03','行程生成','按天生成上午/下午/晚间计划，包含地点、建议时长、交通衔接、天气适配和预算小计。','每一天结构完整；不出现时间重叠；总预算不超过用户预算或明确提示冲突。'),
('FR-04','交通与住宿','通过正式授权 API 查询实时航班、铁路和酒店候选；API 不可用时返回可解释错误，不生成替代数据。','结果标记供应商、查询时间、币种和有效期；价格与库存均来自本次 API 响应。'),
('FR-05','天气','调用 Open-Meteo 获取目标地点与日期的预报，失败时给出可恢复提示。','合法城市查询成功率 ≥95%；超时后 2 次指数退避，总等待上限 5 秒。'),
('FR-06','RAG 与引用','向量 + BM25 检索后用 RRF 融合，答案句子绑定 source_id。','标注集 Top3 命中率 ≥95%；有事实依据的回答引用覆盖率 100%。'),
('FR-07','多轮记忆','使用 thread_id 保存已确认槽位、工具摘要和最近对话。','10 轮内可正确引用目的地、日期、预算；重启容器后会话仍可恢复。'),
('FR-08','过程可视化','Chainlit 展示意图、检索、工具、合成步骤及耗时，隐藏密钥和内部提示词。','每个步骤有状态与耗时；日志无密钥。'),
('FR-09','反馈与追踪','用户可点赞/点踩并填写原因；保存匿名 trace_id。','反馈写入 SQLite；可按日期导出 CSV。'),
('FR-10','安全边界','拒绝提示注入、密钥探测和越权工具参数；外部文本视为不可信数据。','安全用例全部通过；日志与回答不出现 secret。')]
table(doc,['ID','能力','产品规则','验收标准'],reqs,[1.3,3.0,7.3,4.9],8)

doc.add_heading('4 交互与 Agent 流程',level=1)
doc.add_picture(str(flow),width=Cm(16.2)); caption(doc,'图 1 用户请求到可追溯答案的处理流程')
doc.add_heading('4.1 状态机节点',level=2)
table(doc,['节点','输入','输出','失败策略'],[
 ['normalize_input','message, thread_id','标准化文本与上下文','空输入直接提示'],
 ['classify_and_extract','文本与历史槽位','intent, slots, confidence','修复一次；低置信度追问'],
 ['route','意图、槽位完整性','下一节点','未知意图进入边界说明'],
 ['retrieve','query, filters','TopK 文档及分数','无命中时不强行生成事实'],
 ['call_tools','结构化参数','标准 ToolResult','超时、熔断、缓存；失败时明确不可用'],
 ['validate_plan','候选行程与约束','冲突清单','最多修订一次，仍冲突则显式报告'],
 ['compose','证据、工具结果','带引用答案','引用不足则缩小回答范围'],
 ['persist','状态、轨迹、反馈','checkpoint','失败不影响本轮答案，但提示会话不可恢复']],[3.0,4.3,4.2,4.9],8.2)
doc.add_heading('4.2 行程输出结构',level=2)
bullet(doc,'行程概览：目的地、日期、人数、偏好、预算、数据更新时间。')
bullet(doc,'每日计划：时间段、地点、活动、预计时长、地点间交通、费用区间、来源。')
bullet(doc,'总览：住宿候选、交通候选、预算汇总、天气与装备提示。')
bullet(doc,'限制：尚未核验的信息、第三方 API 缺失、用户需二次确认的事项。')

doc.add_heading('5 系统架构与技术设计',level=1)
doc.add_picture(str(arch),width=Cm(16.4)); caption(doc,'图 2 轻量级系统架构')
doc.add_heading('5.1 技术选型',level=2)
table(doc,['层级','选型','用途','轻量化理由'],[
 ['语言','Python 3.10+','核心开发','AI 生态完整，课程适配'],['前端','Chainlit','聊天、步骤、引用','无需维护独立前端工程'],['API','FastAPI + Uvicorn','REST、SSE、健康检查','异步、文档自动生成'],['编排','LangGraph','状态机、条件路由、检查点','单图替代多服务 Agent'],['LLM','DeepSeek API','意图、槽位、生成','中文能力与 API 成本优势'],['结构化输出','Pydantic + LangChain','输出模式与校验','减少重复调用'],['RAG','LangChain','加载、切分、检索','与 LangGraph 衔接'],['向量库','ChromaDB 独立容器','持久化向量索引','部署简单、官方镜像'],['Embedding','BGE API；本地 bge-small 备用','文档向量化','API 优先，无 GPU 依赖'],['检索','向量 + BM25 + RRF','混合召回','兼顾语义与专有名词'],['记忆','SQLite Checkpointer','会话与审计','无需额外数据库'],['测试','pytest + 自建评测集','功能与指标','可在 CI 离线复现'],['部署','Docker Compose','一键启动','统一环境、便于答辩']],[2.7,4.0,4.5,5.2],8)
doc.add_heading('5.2 组件与端口',level=2)
table(doc,['服务','端口','职责','持久化','健康检查'],[
 ['web','8000','Chainlit UI 与反向代理入口','无','GET /healthz'],['api','8001','FastAPI、LangGraph、工具与 SSE','./data/sqlite','GET /health'],['chroma','8002','向量存储与查询','chroma_data volume','/api/v2/heartbeat']],[2.4,1.7,6.0,3.2,3.2])
doc.add_paragraph('Compose 使用非 root 用户、只读根文件系统（必要目录挂载可写卷）、资源限制和 restart: unless-stopped。密钥仅通过 .env 或部署平台 Secret 注入，.env 加入 .gitignore。')
doc.add_heading('5.3 API 契约',level=2)
table(doc,['方法与路径','请求','响应','说明'],[
 ['POST /api/v1/chat','message, thread_id?, stream=true','SSE: meta/step/token/final/error','主对话接口'],['POST /api/v1/feedback','trace_id, score, reason?','status','用户反馈'],['POST /api/v1/admin/index','source_path, rebuild','job_id, status','开发环境启用，生产鉴权'],['GET /api/v1/traces/{id}','trace_id','节点、耗时、工具摘要','隐藏提示词与密钥'],['GET /health','无','status, deps','容器存活与依赖状态']],[3.8,4.0,5.0,3.7],8.2)
doc.add_heading('5.4 数据模型',level=2)
table(doc,['对象','关键字段','约束'],[
 ['TravelRequest','message, thread_id, destination, days, dates, people_number, preferences, budget','日期合法；days 1-14；budget ≥0'],['IntentResult','intent, confidence, slots, missing_slots','intent 为枚举；confidence 0-1'],['ToolResult','provider, status, data, fetched_at, expires_at, error_code','数据必须包含真实供应商和时间戳'],['SourceChunk','source_id, title, url, text, score, updated_at, source_type','URL 与引用一一对应；source_type 区分 private 与 public'],['ItineraryDay','date, periods[], cost_min, cost_max, warnings','时间不重叠；金额单位一致'],['Trace','trace_id, thread_id, node_timings, tool_calls, model_usage','不保存密钥；敏感输入脱敏']],[3.3,8.0,5.1],8.2)

doc.add_heading('6 数据与 RAG 设计',level=1)
doc.add_heading('6.1 推荐数据源与链接',level=2)
rows=[
 ['TravelPlanner Benchmark','复杂约束行程规划评测；含数据库与验证工具','评测/测试数据','研究用途，按仓库许可核验','https://github.com/OSU-NLP-Group/TravelPlanner'],
 ['Wikivoyage Dumps','目的地、路线、旅行主题的文本语料','RAG 主语料','遵循 CC BY-SA 与署名要求','https://en.wikivoyage.org/wiki/Wikivoyage:Database_dump'],
 ['GeoNames','城市、地名、经纬度与别名','地名标准化','CC BY 4.0，需署名','https://www.geonames.org/export/'],
 ['OpenStreetMap / Geofabrik','POI、道路与地理要素区域切片','离线 POI 扩展','ODbL，需署名与数据库许可合规','https://download.geofabrik.de/'],
 ['Open-Meteo','地理编码与天气预报 API','实时天气工具','按官方条款署名；无需 API Key','https://open-meteo.com/en/docs'],
 ['OpenTripMap','景点与兴趣点 API','实时/准实时 POI 工具','需要 API Key，遵循服务条款','https://opentripmap.io/product'],
 ['Wikidata Query Service','景点实体、坐标、分类与多语言名称','POI 补充与实体对齐','CC0 数据；注意查询限流','https://query.wikidata.org/']]
table(doc,['数据源','内容','系统用途','许可与风险','链接'],rows,[3.2,4.0,2.8,4.1,4.0],7.5)
doc.add_paragraph('数据选择原则：课程原型优先使用许可清晰、可复现的数据；不把受登录、反爬或商业条款限制的网站抓取结果写入仓库。生产级价格和库存应更换为正式签约的供应商 API。')
doc.add_heading('6.2 知识库加工',level=2)
doc.add_paragraph('覆盖范围为中国全境，包括 34 个省级行政区范围内的省会、地级市、自治州、主要县级旅游目的地及跨城市线路。知识文档按省份、城市、景点类型和更新时间打标签，支持全国统一检索与城市过滤。')
reset_number()
number(doc,'采集：保存 source_url、license、retrieved_at、language 与内容哈希。')
number(doc,'清洗：去导航、广告、重复段落、失效链接和明显错误；城市与景点名称对齐 GeoNames/Wikidata。')
number(doc,'切分：按标题语义切分，建议 350-600 中文字符，重叠 60-100 字；保留父文档 ID。')
number(doc,'索引：BGE Embedding 写入 ChromaDB；BM25 建立词法索引；索引版本绑定数据快照。')
number(doc,'检索：向量 Top10 + BM25 Top10，经 RRF 合并取 Top3；可按城市、类别、时间过滤。')
number(doc,'生成：只把 Top3 证据和工具结果送入 LLM；每个事实句绑定 source_id。')
doc.add_heading('6.3 评测集设计',level=2)
table(doc,['集合','规模','组成','用途'],[
 ['intent_eval.jsonl','每类至少 30 条，共 ≥210 条','标准表达、口语、省略、复合意图、越界输入','意图准确率与槽位 F1'],
 ['rag_eval.jsonl','≥100 条','query、相关 source_id、难负例','Recall@3、MRR、nDCG@3'],
 ['plan_eval.jsonl','≥50 条','预算、天数、偏好、硬约束和参考检查器','约束满足率、完整率'],
 ['safety_eval.jsonl','≥30 条','提示注入、密钥探测、恶意 URL、超长输入','拒绝率与泄露检查'],
  ['boundary_eval.jsonl','≥40 条','只在工具可得、只在知识库可得、两者皆无、工具失败与降级','证据绑定率、无关知识引入、无证据拒答率']],[3.5,2.5,7.2,4.1],8.2)

doc.add_heading('6.4 私有知识与证据口径',level=2)
doc.add_paragraph('口径对齐：任务书评分表要求“生成回答 100% 基于私有知识、无幻觉”。本系统的“私有知识”定义为系统自有的可信数据，只包含两个来源，不使用模型参数化记忆。')
table(doc,['证据类型','来源','标识','覆盖的问题','不允许的行为'],[
 ['知识证据','本地私有知识库（自建与人工校订条目、许可公开语料 Wikivoyage / GeoNames / Wikidata）','source_id','景点、美食、贴士、目的地介绍等稳定事实','以模型记忆充当知识来源'],
 ['工具证据','本次请求内正式 API 的真实响应（天气、交通、酒店、路线）','tool_call_id','价格、余票、天气、营业时间等实时数值','在缓存有效期之外补全、估算或改写价格'],
 ['无证据','两个来源都没有可用结果','—','—','生成任何事实性内容；应明确未知或说明服务暂不可用']],[2.3,6.4,2.2,3.3,3.5],7.6)
doc.add_paragraph('绑定要求：答案中每个事实句必须绑定 source_id 或 tool_call_id，未绑定的事实句不得出现在答案中；工具证据必须携带供应商、查询时间与有效期。该要求与第 8 章“依据覆盖率 100%”是同一口径的两种表述。')
doc.add_paragraph('私有属性：知识库文档在 manifest 中登记 source_type（private / public），自建与人工校订内容标记为 private，公开语料标记为 public；评测报告分别给出两类来源的文档数、占比与检索命中率。')
doc.add_paragraph('边界管控：只有工具能回答的实时问题（价格、余票、天气）不得用知识库内容替代；只有知识库能回答的介绍类问题不得用模型记忆补全；价格与库存必须来自本次 API 响应，无可用结果时明确说明服务暂不可用。')

doc.add_heading('7 外部 API 与降级策略',level=1)
table(doc,['能力','首选实现','超时与缓存','降级'],[
 ['天气','Open-Meteo Geocoding + Forecast','连接 1s/总 3s；缓存 30 分钟','返回最近缓存；无缓存则说明暂不可用'],
 ['景点','OpenTripMap 或 Wikidata','总 4s；缓存 24 小时','RAG 本地知识库'],
 ['路线距离','openrouteservice/高德适配器','总 4s；缓存 24 小时','直线距离 + 明确“估算”'],
 ['航班','Travelport Flights API','总 5s；报价按供应商有效期缓存','失败时返回不可用，不生成票价'],
 ['火车','Travelport Universal API Rail 或已签约铁路供应商 API','总 5s；缓存 5 分钟','失败时返回不可用并保留重试入口'],
 ['酒店','Travelport Stays API','总 5s；报价按供应商有效期缓存','失败时返回不可用，不生成价格区间'],
 ['LLM','DeepSeek API','总 15s；最多重试 1 次','返回结构化错误与稍后重试提示'],
 ['Embedding','BGE-M3 API','批量请求；永久缓存内容哈希','本地 bge-small-zh-v1.5']],[2.3,4.3,4.0,6.0],8)
doc.add_paragraph('所有适配器实现统一 Provider 接口，输出 ToolResult。熔断条件为 60 秒内连续 5 次失败，熔断 60 秒；恢复阶段只放行 1 个真实探测请求。缓存只复用未超过供应商有效期的真实响应；无可用真实结果时必须明确说明服务暂不可用。')

doc.add_heading('8 非功能需求与指标',level=1)
table(doc,['维度','指标','目标值','测量方法'],[
 ['功能','七类意图通过率','≥95%','每类固定用例自动执行'],['意图','宏平均准确率','≥90%','intent_eval 混淆矩阵'],['槽位','必填槽位 F1','≥90%','标注槽位逐字段计算'],['检索','Recall@3','≥95%','rag_eval 相关文档命中'],['检索','MRR','≥0.90','首个相关结果倒数排名均值'],['生成','依据覆盖率','100%','事实句是否可映射到知识证据（source_id）或工具证据（tool_call_id）'],['生成','证据绑定率','100%','每个事实句必须绑定 source_id 或 tool_call_id，未绑定不得输出'],['生成','无关知识引入','0 条/答复','引用覆盖率报告逐句判定；rag_eval 含难负例'],['生成','幻觉率','0% 高风险；≤2% 一般事实','人工双人复核 + 自动规则'],['规划','硬约束满足率','100%','日期、预算、时间重叠检查器'],['性能','首 token P95','≤2 秒','从服务收到请求到首个 SSE token；20次'],['性能','完整回答 P95','记录基线，不作为2秒门禁','独立记录 total_latency_ms'],['性能','外部工具调用 P95','≤5 秒/工具','独立记录 provider latency'],['稳定性','连续 100 请求成功率','≥99%','固定负载测试'],['部署','冷启动可用时间','≤60 秒','compose up 到 health=healthy'],['资源','空闲内存','≤1.5 GB','docker stats'],['安全','高危依赖与镜像漏洞','0 个','pip-audit/Bandit/Trivy'],['可维护','核心代码覆盖率','≥80%','pytest-cov；关键路由 ≥90%']],[2.4,4.2,4.3,5.8],8)
doc.add_paragraph('说明：任务书中的 2 秒明确解释为首 token P95。验收报告仍同时给出应用内处理时间、首 token 时间、完整回答时间和第三方工具时间，以定位性能瓶颈。')
doc.add_paragraph('说明：任务书评分表的“生成回答 100% 基于私有知识”按 6.4 的口径解释为：事实句 100% 可映射到知识证据或工具证据，模型参数化记忆不参与事实生成；实时数值类事实只能来自本次工具响应。')

doc.add_heading('9 测试策略与流程',level=1)
doc.add_picture(str(testflow),width=Cm(16.4)); caption(doc,'图 3 从提交到合并的自动化测试与审查流程')
doc.add_heading('9.1 测试层级',level=2)
table(doc,['层级','范围','关键用例','通过标准'],[
 ['单元测试','槽位、路由、RRF、预算、引用、适配器','空值、边界、异常、重试、熔断','核心模块覆盖率 ≥90%'],
 ['契约测试','DeepSeek、天气、POI、交通、酒店适配器','Schema、超时、429、5xx、坏 JSON','全部 Provider 契约一致'],
 ['API 集成','/chat、SSE、feedback、health','正常、断流、重复请求、并发','状态码和事件序列正确'],
 ['RAG 评测','切分、召回、融合、引用','同义词、专名、难负例、无答案','Recall@3 ≥95%'],
 ['端到端','7 类意图与多轮行程','正式 API 测试/生产环境','核心旅程 100% 通过'],
 ['性能','首 token、完整响应、检索、并发','预热/冷启动、缓存命中/未命中','达到第 8 章目标'],
 ['故障演练','LLM/API/Chroma 超时与重启','断网、限流、索引损坏、磁盘只读','可降级且错误可解释'],
 ['安全测试','依赖、镜像、Secret、提示注入','泄密、SSRF、超长输入、恶意来源','无高危漏洞和秘密泄露']],[2.2,4.0,6.4,4.1],8)
doc.add_heading('9.2 核心验收用例',level=2)
table(doc,['用例','前置','步骤','预期'],[
 ['E2E-01 三日行程','知识库已索引；天气可用','输入“2人预算3000元，规划3天西安历史文化游”','抽取人数/预算/天数/偏好；调用 RAG 与天气；输出不超预算的分日行程和引用'],
 ['E2E-02 缺槽追问','新会话','输入“帮我规划旅行”','只追问目的地和天数等最少必需信息，不猜测'],
 ['E2E-03 多轮修订','已生成西安行程','输入“第二天改成亲子活动，预算不变”','继承城市、天数、预算，只修改第二天并重新校验'],
 ['E2E-04 API 失败','天气 Provider 返回 503','请求明日天气与行程','显示天气暂不可用；仍基于 RAG 生成其余内容；记录降级'],
 ['E2E-05 小众地点','全国知识库已索引','询问县级小众地点细节','检索对应省市资料；无可靠证据则明确未知，不编造'],
 ['E2E-06 提示注入','知识文本含恶意指令','正常查询该知识','忽略资料中的指令，只提取事实；不输出系统提示或密钥'],
 ['E2E-07 重启恢复','已有 thread_id','重启 api 容器后继续追问','从 SQLite 恢复上下文']],[2.5,3.4,6.0,5.0],8)
doc.add_heading('9.3 执行与报告',level=2)
reset_number()
number(doc,'开发者本地执行 pre-commit、单元测试，并使用测试环境凭据执行真实 API E2E。')
number(doc,'PR CI 并行执行 lint/type/security、pytest、RAG 快速评测和 Secret 扫描。')
number(doc,'CI 构建 Compose 镜像，启动服务并轮询健康检查，执行 API smoke test。')
number(doc,'主分支或每日任务运行完整 RAG、性能与真实 API 沙盒测试；失败不覆盖历史基线。')
number(doc,'产出 junit.xml、coverage.xml、rag_metrics.json、performance.json、trivy.sarif 和失败样例。')

doc.add_heading('10 GitHub Actions 自动代码审查机制',level=1)
doc.add_heading('10.1 工作流与门禁',level=2)
table(doc,['工作流','触发','步骤','是否阻止合并'],[
 ['pr-quality.yml','pull_request','Ruff format/check、Mypy、Bandit、pytest-cov、detect-secrets','是'],
 ['rag-eval.yml','PR 且 RAG 文件变化','建立临时索引、运行快速集、比较基线','Recall@3 下降 >2 个百分点或低于95%则阻止'],
 ['container.yml','pull_request / main','Buildx、Compose smoke、Trivy FS/Image、生成 SBOM','高危漏洞或启动失败则阻止'],
 ['ai-review.yml','内部 PR；显式标签 ai-review','把最小化 diff 发送给 DeepSeek，输出结构化风险评论','否；仅辅助人工'],
 ['nightly.yml','每日定时 / 手动','完整评测、真实 API 沙盒、性能趋势','不直接阻止既有提交；创建 Issue']],[3.0,3.4,7.0,4.0],8)
doc.add_heading('10.2 分支保护规则',level=2)
bullet(doc,'main 禁止直接推送；至少 1 名非作者批准；所有必需检查通过后方可合并。')
bullet(doc,'CODEOWNERS：Agent/RAG、部署、安全敏感路径由对应成员复核。')
bullet(doc,'开启 Dependabot；依赖升级 PR 自动运行全套测试，禁止自动合并主版本升级。')
bullet(doc,'工作流权限默认 contents: read；只有评论任务使用 pull-requests: write；第三方 Action 固定到 commit SHA。')
bullet(doc,'Fork PR 不注入任何 API Secret；AI Review 和真实 API 测试仅在可信分支执行。')
doc.add_heading('10.3 AI Review 规则',level=2)
doc.add_paragraph('AI 审查输入仅包含 PR diff、项目规则和测试摘要，不发送 .env、用户数据、完整知识库或仓库秘密。输出按“严重级别、文件与行号、问题、证据、修复建议”组织；只评论可证实问题，不自动修改或批准 PR。人工评审者必须核验 AI 建议，AI 评论不替代必需测试和 CODEOWNER 批准。')
doc.add_heading('10.4 建议仓库结构',level=2)
p=doc.add_paragraph(); r=p.add_run('''app/\n  api/          FastAPI 路由与 SSE\n  agent/        LangGraph 状态、节点、路由\n  models/       Pydantic 请求与结果模型\n  rag/          全国数据采集、分区索引、混合检索\n  tools/        provider 接口与正式 API 适配器\n  ui/           Chainlit 入口\ndata/           许可说明与可公开的小型评测数据\nevals/          intent、RAG、plan、安全评测集\ntests/          unit、integration、e2e\ndocs/           架构、部署、测试、AI 开发档案、会议纪要\n.github/        workflows、CODEOWNERS、PR 模板\nDockerfile  compose.yaml  pyproject.toml  .env.example'''); r.font.name='Consolas'; r._element.rPr.rFonts.set(qn('w:eastAsia'),'Microsoft YaHei'); r.font.size=Pt(9)

doc.add_heading('11 Docker 部署与运维',level=1)
doc.add_heading('11.1 部署验收',level=2)
reset_number()
number(doc,'复制 .env.example 为 .env，填写 DeepSeek、Travelport、天气、景点和路线服务的正式 API 凭据。')
number(doc,'执行 docker compose up -d --build。')
number(doc,'等待 web、api、chroma 三个服务状态 healthy，60 秒内完成。')
number(doc,'通过自有主机 HTTPS 域名访问系统，执行天气、目的地、三日行程和多轮修改四个冒烟场景。')
number(doc,'执行 docker compose restart api 后继续原 thread_id，验证 SQLite 持久化。')
number(doc,'执行 docker compose down 后数据卷保留；使用带 -v 的清理命令前必须备份。')
doc.add_heading('11.2 配置项',level=2)
table(doc,['变量','必需','示例/默认','说明'],[
 ['APP_ENV','是','production','自有主机使用生产配置'],['DEEPSEEK_API_KEY','是','Secret','不得写入日志'],['DEEPSEEK_MODEL','否','deepseek-chat','以供应商当前可用模型为准'],['TRAVELPORT_CREDENTIALS','是','Secret 引用','航班、酒店和铁路正式凭据'],['CHROMA_HOST','否','chroma','Compose 服务名'],['SQLITE_PATH','否','/data/app.db','挂载持久卷'],['PUBLIC_BASE_URL','是','https://travel.example.com','自有主机外部访问地址'],['TOOL_TIMEOUT_SECONDS','否','5','外部工具总超时'],['LOG_LEVEL','否','INFO','生产禁止 DEBUG 泄露输入']],[4.2,2.4,4.2,6.0],8.2)
doc.add_heading('11.3 可观测性',level=2)
bullet(doc,'日志使用 JSON，字段包含 trace_id、thread_id_hash、node、provider、latency_ms、status、error_code；不记录密钥和完整个人信息。')
bullet(doc,'每次回答返回 trace_id；Chainlit 展示节点耗时摘要；评测环境保存 LangGraph 轨迹。')
bullet(doc,'健康检查区分 liveness 与 readiness，Chroma、正式 API 凭据或关键配置不可用时 readiness=false。')
bullet(doc,'自有主机通过 Caddy 或 Nginx 终止 TLS，只暴露 80/443；API、Chroma 与 SQLite 不直接暴露公网。')

doc.add_heading('12 竞品与开源参考',level=1)
table(doc,['项目','可借鉴点','需避免/裁剪','链接'],[
 ['LangChain Travel Planner','Docker、工具抽象、Smoke test','Flask 改为课程指定 FastAPI；外部数据改接正式 API','https://github.com/MUSE-CODE-SPACE/langchain-travel-planner'],
 ['TwoSigma Travel Planner Agent','LangGraph + FastAPI、酒店/航班/天气/景点分工','React 与多 Agent 对本课题过重','https://github.com/TwoSigma-Labs/Travel-Planner-Agent'],
 ['LangChain Samples Travel Planner','官方示例的状态图、线程与 Human in the Loop','LangSmith 云部署不是必需依赖','https://github.com/langchain-samples/demos-travel-planner-agent'],
 ['AI Trip Planner','实时天气、地点、预算、汇率工具和 FastAPI','Streamlit 改为 Chainlit；核验外部数据许可','https://github.com/Balaji-253/AI-Trip-Planner'],
 ['JourneyPilot','来源支撑的行程、RAG、Docker、自托管','功能范围较大，首版不做地图和 PDF','https://github.com/Lagom-TA/JourneyPilot'],
 ['TravelPlanner Benchmark','约束规划数据库、验证器与评测方法','不直接复制数据到产品库，先核验许可','https://github.com/OSU-NLP-Group/TravelPlanner']],[3.4,5.3,5.0,4.4],7.5)
doc.add_paragraph('参考策略：借鉴架构与测试方法，不直接照搬代码。纳入任何第三方代码前检查许可证、依赖安全和维护状态，并在 NOTICE 或依赖清单中保留归属。')

doc.add_heading('13 项目计划与分工',level=1)
table(doc,['阶段','周次','关键任务','里程碑'],[
 ['需求与设计','第1周','确定范围、数据许可、PRD、架构、评测口径','PRD 评审通过'],['骨架与数据','第2周','仓库规范、Compose、FastAPI/Chainlit、全国数据清洗与分区索引','自有主机基础服务可启动'],['Agent 与工具','第3周','意图槽位、LangGraph、天气/POI、Travelport 适配器','七类意图主路径可用'],['RAG 与记忆','第4周','混合检索、引用、SQLite Checkpoint、反馈','Top3 指标达到基线'],['测试与 CI','第5周','真实 API 自动测试、Actions、镜像扫描、故障演练','所有门禁通过'],['优化与文档','第6周','首 token 优化、部署文档、用户手册、AI 开发档案','自有主机全新部署复现'],['答辩准备','第7周','PPT、演示脚本、测试报告、风险预案','5分钟演示稳定完成']],[2.5,2.0,8.0,5.5],8.2)
table(doc,['角色','主责','必须留下的证据'],[
 ['项目经理','范围、排期、例会、PRD、答辩整合','计划表、会议纪要、风险清单、验收记录'],['Agent/RAG 开发','LangGraph、意图槽位、检索、评测','设计记录、评测脚本、指标报告、人工修正日志'],['平台/测试开发','FastAPI、Chainlit、工具适配、Docker、CI','接口文档、测试报告、工作流、部署录像']],[3.2,7.4,7.2])

doc.add_heading('14 风险与应对',level=1)
table(doc,['风险','概率/影响','预防','触发后的处理'],[
 ['第三方 API 限流或停服','高/高','缓存、超时、配额监控、Provider 抽象','使用有效真实缓存；无缓存则明确暂不可用'],['实时价格不可获得','中/高','使用正式授权 API，保存报价有效期','拒绝生成价格并引导稍后重试'],['RAG 全国索引规模增长','中/高','按省市分区、增量索引、元数据过滤','扩容 Chroma 持久卷并重建受影响分区'],['RAG 指标不足','中/高','清洗、分层切分、混合检索、难负例','调整 chunk、权重和过滤器并记录对比'],['LLM 幻觉','中/高','结构化输出、引用约束、无证据不回答','缩小回答范围并明确未知'],['首 token 指标受网络影响','高/高','SSE、并行工具、连接池、真实响应缓存','监控网络与供应商耗时，优化首段生成'],['Secret 泄露','低/高','.env 忽略、Secret 扫描、最小权限','立即吊销轮换，清理历史并复盘'],['自有主机故障','中/高','健康检查、磁盘监控、数据库备份、镜像版本化','在同一主机回滚镜像并恢复持久卷备份'],['范围膨胀','中/中','七类意图为 MVP，变更需评审','延期非目标功能']],[3.2,2.4,6.1,6.1],8)

doc.add_heading('15 完成定义与评审清单',level=1)
checks=['七类意图均有正常、缺槽、异常用例并通过','中国全境知识库完成分区索引与抽样验收','RAG Recall@3 ≥95%，引用依据覆盖率 100%','多轮 10 轮与容器重启恢复测试通过','首 token P95 ≤2 秒，完整响应与外部工具耗时分别报告','Docker Compose 在自有主机 60 秒内健康','航班、铁路、酒店结果均来自正式 API 且带供应商时间戳','无高危依赖/镜像漏洞，无 Secret 入库','PR 合并门禁与 CODEOWNERS 已启用','所有数据源记录许可、时间、URL 和版本','事实句 100% 绑定知识证据或工具证据，无未溯源内容','评测报告分别给出私有来源与公开来源的文档占比与命中率']
for c in checks: bullet(doc,'□ '+c)

doc.add_heading('附录 A 参考链接',level=1)
links=[
('DeepSeek API 文档','https://api-docs.deepseek.com/'),('LangGraph 文档','https://docs.langchain.com/oss/python/langgraph/overview'),('FastAPI 文档','https://fastapi.tiangolo.com/'),('Chainlit 文档','https://docs.chainlit.io/'),('Chroma 文档','https://docs.trychroma.com/'),('Docker Compose 文档','https://docs.docker.com/compose/'),('GitHub Actions 文档','https://docs.github.com/actions'),('Travelport Universal API','https://support.travelport.com/webhelp/uapi/Content/Introduction.htm'),('Travelport Flights 与 Stays API','https://developer.travelport.com/resources/devkits-and-downloads'),('Open-Meteo API','https://open-meteo.com/en/docs'),('GeoNames 数据导出','https://www.geonames.org/export/'),('Wikivoyage Dumps','https://en.wikivoyage.org/wiki/Wikivoyage:Database_dump'),('TravelPlanner Benchmark','https://github.com/OSU-NLP-Group/TravelPlanner')]
for name,url in links:
    p=doc.add_paragraph(style='List Bullet'); add_hyperlink(p,name,url)

doc.add_heading('附录 B 待确认事项',level=1)
doc.add_paragraph('开发前需要登记三项实施参数：Travelport 或等价正式供应商的最终账号、环境端点、请求限频与生产授权范围；自有主机的域名、CPU、内存、磁盘、操作系统和公网带宽；中国全境知识库首批数据快照日期与定期更新频率。')

# footer and metadata
for section in doc.sections:
    footer=section.footer.paragraphs[0]; footer.alignment=WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run('旅游规划助手 PRD  v1.2').font.size=Pt(8)
doc.core_properties.title='旅游规划助手产品需求文档'
doc.core_properties.subject='人工智能创新开发实践课程设计'
doc.core_properties.author='课程设计项目组'
doc.save(OUT)
print(OUT)
