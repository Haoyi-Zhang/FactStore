"""Draw the two defined storage schematics directly as vector PDFs.

Requires Python 3.9+ and Matplotlib; no TeX engine or network is used.
"""
import argparse
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Polygon, Circle, FancyArrowPatch

plt.rcParams.update({'font.family':'STIXGeneral', 'mathtext.fontset':'stix',
                     'font.size':9, 'pdf.fonttype':42, 'ps.fonttype':42,
                     'text.usetex':False, 'axes.unicode_minus':False})
CM = 1 / 2.54

class Drawing:
    def __init__(self, bounds):
        x0,y0,x1,y1 = bounds
        self.fig = plt.figure(figsize=((x1-x0)*CM,(y1-y0)*CM))
        self.ax = self.fig.add_axes([0,0,1,1], xlim=(x0,x1), ylim=(y0,y1))
        self.ax.set_aspect('equal'); self.ax.axis('off')
        self.nodes = {}
        self.labels = []

    def text(self,x,y,label,size=9,ha='center',va='center'):
        artist=self.ax.text(x,y,label,fontsize=size,ha=ha,va=va,
                            linespacing=1.15,clip_on=False,zorder=4)
        self.labels.append([artist,None])
        return artist

    def box(self,name,x,y,w,h,label,dashed=False,shape='rect',size=9):
        style = {'edgecolor':'black','facecolor':'white','linewidth':.55,
                 'linestyle':(0,(4,3)) if dashed else 'solid', 'zorder':2}
        if shape == 'diamond':
            obj = Polygon([(x-w/2,y),(x,y+h/2),(x+w/2,y),(x,y-h/2)],**style)
        elif shape == 'circle': obj = Circle((x,y),w/2,**style)
        else: obj = Rectangle((x-w/2,y-h/2),w,h,**style)
        self.ax.add_patch(obj); self.text(x,y,label,size)
        self.labels[-1][1]=(x-w/2,y-h/2,x+w/2,y+h/2)
        self.nodes[name] = (x,y,w,h,shape)

    def point(self,name,side):
        x,y,w,h,_ = self.nodes[name]
        return {'n':(x,y+h/2),'s':(x,y-h/2),'e':(x+w/2,y),
                'w':(x-w/2,y),'nw':(x-w/2,y+h/2),
                'ne':(x+w/2,y+h/2),'c':(x,y)}[side]

    def line(self,points,dashed=False):
        xs,ys=zip(*points)
        self.ax.plot(xs,ys,color='black',linewidth=.55,
                     linestyle=(0,(4,3)) if dashed else 'solid',zorder=1)

    def arrow(self,p,q,dashed=False,via=()):
        points=[p,*via,q]
        if via:self.line(points[:-1],dashed)
        self.ax.add_patch(FancyArrowPatch(points[-2],points[-1],
                         arrowstyle='-|>',mutation_scale=7.5,linewidth=.55,
                         color='black',shrinkA=0,shrinkB=0,
                         linestyle=(0,(4,3)) if dashed else 'solid',zorder=1))

    def edge(self,a,b,sa='s',sb='n',**kwargs):
        self.arrow(self.point(a,sa),self.point(b,sb),**kwargs)

    def frame(self,x,y,w,h):
        self.ax.add_patch(Rectangle((x,y),w,h,fill=False,edgecolor='black',
                                   linewidth=.55,linestyle=(0,(4,3)),zorder=0))

    def save(self,out,name):
        # Fixed physical canvas: do not enlarge or rasterize the scientific figure.
        self.fig.canvas.draw()
        renderer=self.fig.canvas.get_renderer()
        x0,x1=self.ax.get_xlim();y0,y1=self.ax.get_ylim()
        for artist,node in self.labels:
            b=artist.get_window_extent(renderer).transformed(self.ax.transData.inverted())
            if b.x0<x0-.02 or b.x1>x1+.02 or b.y0<y0-.02 or b.y1>y1+.02:
                raise ValueError(name+': clipped label '+repr(artist.get_text()))
            if node and (b.x0<node[0]-.02 or b.x1>node[2]+.02 or b.y0<node[1]-.02 or b.y1>node[3]+.02):
                raise ValueError(name+': label exceeds node '+repr(artist.get_text()))
        self.fig.savefig(out/(name+'.pdf'),format='pdf',
                         metadata={'Creator':'Python Matplotlib','Producer':None,
                                   'CreationDate':None,'ModDate':None})
        plt.close(self.fig)





def draw(out):
    # The dependency-closed mix combines reads across a committed endpoint.
    d=Drawing((-3.5,-2.55,3.5,1.9))
    d.text(-3.25,1.65,'(a) Separate reads',ha='left')
    d.box('old',-1.75,.95,2.7,.68,r'Old $M,S$')
    d.box('new',1.75,.95,2.7,.68,r'New $F,D$')
    d.edge('old','new',sa='e',sb='w');d.text(0,1.55,'commit')
    d.box('mix',0,-.05,4.2,.85,'Closed mixed value\n'+r'neither $\mathcal{C}_0$ nor $\mathcal{C}_1$',dashed=True)
    d.edge('old','mix',sa='s',sb='nw');d.edge('new','mix',sa='s',sb='ne')
    d.line([(-3.25,-.75),(3.25,-.75)])
    d.text(-3.25,-1.02,'(b) One selector',ha='left')
    d.box('root',-1.75,-1.68,2.7,.72,'One root\nold or new')
    d.box('txn',1.75,-1.68,2.7,.72,'One read txn.\nold or new')
    d.text(-1.75,-2.27,'physical selector');d.text(1.75,-2.27,'logical selector')
    d.save(out,'endpoint-selectors')

    # Each framed row is a different representation, not a time sequence.
    d=Drawing((-1.65,-8.85,11.75,.95))
    d.frame(-1.55,-.65,10.15,1.5);d.text(3.525,.60,'FrontierStore')
    for name,x,label in [('root',0,'Root'),('segments',3.5,'Base + deltas'),('check',7,'Segment checker')]:
        d.box(name,x,-.05,2.7,.85,label)
    d.edge('root','segments',sa='e',sb='w');d.edge('segments','check',sa='e',sb='w')
    d.frame(-1.55,-3.42,6.65,1.5);d.text(1.775,-2.18,'SQLite-N')
    d.box('db',0,-2.82,2.7,.85,'Normalized DB')
    d.box('read',3.5,-2.82,2.7,.85,'One read txn.')
    d.edge('db','read',sa='e',sb='w')
    d.frame(-1.55,-8.65,13.15,4.4);d.text(5.025,-4.5,'SQLite-R')
    d.box('pair',-.1,-6.65,1.8,1.0,'Root')
    d.box('image',3,-5.45,2.65,.95,'SQLite image')
    d.box('export',3,-7.85,2.65,.95,'Canonical export')
    d.box('sqlite',6.5,-5.45,2.65,.95,'SQLite reader')
    d.box('parser',6.5,-7.85,2.65,.95,'Standalone parser')
    d.box('equal',10.15,-6.65,2.55,1.1,'Decoded states\nmust be equal')
    p=d.point('pair','e')
    for dst in ['image','export']:
        q=d.point(dst,'w');d.arrow(p,q,via=((1.15,p[1]),(1.15,q[1])))
    d.edge('image','sqlite',sa='e',sb='w');d.edge('export','parser',sa='e',sb='w')
    for src,delta in [('sqlite',.2),('parser',-.2)]:
        p=d.point(src,'e');q=(d.point('equal','w')[0],-6.65+delta)
        d.arrow(p,q,via=((8.35,p[1]),(8.35,q[1])))
    d.save(out,'representation-paths')

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=Path(__file__).resolve().parent)
    out=parser.parse_args().output
    out.mkdir(parents=True,exist_ok=True)
    draw(out)
