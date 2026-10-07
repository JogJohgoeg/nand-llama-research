#!/usr/bin/env python3
"""Bit-parallel NAND/LATCH simulator on a Vulkan GPU (wgpu / WGSL compute).

Lanes are independent stimuli, so no cross-lane dependence exists: each GPU
invocation owns one u32 word (32 lanes) and walks the whole record list in its
canonical order (already topological; LATCH records yield the stored state),
then latches capture their D wires, for T clocks inside one dispatch.
Values are laid out [wire][word] so neighbouring invocations touch neighbouring
words. Semantics are exactly golden.Netlist.step: all state starts at zero.
"""
from pathlib import Path
import ctypes as ct,json,os,subprocess,sys,time
import numpy as np
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
sys.path[:0]=[str(ROOT)]
from golden import Netlist

SHADER='''
struct P { n_in:u32, n_rec:u32, n_out:u32, n_lat:u32, W:u32, T:u32, t0:u32, pad:u32 };
@group(0) @binding(0) var<storage,read> rec: array<u32>;
@group(0) @binding(1) var<storage,read> inp: array<u32>;
@group(0) @binding(2) var<storage,read_write> val: array<u32>;
@group(0) @binding(3) var<storage,read_write> st: array<u32>;
@group(0) @binding(4) var<storage,read_write> outp: array<u32>;
@group(0) @binding(5) var<storage,read> latd: array<u32>;
@group(0) @binding(6) var<uniform> p: P;
@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let w = gid.x;
  if (w >= p.W) { return; }
  let W = p.W;
  let nw = 2u + p.n_in + p.n_rec;
  let base = nw - p.n_out;
  val[w] = 0u;
  val[W + w] = 0xffffffffu;
  for (var t = 0u; t < p.T; t = t + 1u) {
    for (var i = 0u; i < p.n_in; i = i + 1u) {
      val[(2u + i) * W + w] = inp[(t * p.n_in + i) * W + w];
    }
    var k = 0u;
    for (var r = 0u; r < p.n_rec; r = r + 1u) {
      let a = rec[2u * r];
      let wire = 2u + p.n_in + r;
      if ((a & 0x80000000u) != 0u) {
        val[wire * W + w] = st[k * W + w];
        k = k + 1u;
      } else {
        let b = rec[2u * r + 1u];
        val[wire * W + w] = ~(val[a * W + w] & val[b * W + w]);
      }
    }
    for (var j = 0u; j < p.n_lat; j = j + 1u) {
      st[j * W + w] = val[latd[j] * W + w];
    }
    for (var o = 0u; o < p.n_out; o = o + 1u) {
      outp[((p.t0 + t) * p.n_out + o) * W + w] = val[(base + o) * W + w];
    }
  }
}
'''


def shader(vec):
    """vec=1: u32 per invocation (32 lanes); vec=4: vec4<u32> (128 lanes)."""
    if vec==1:return SHADER
    t=SHADER.replace('var<storage,read> inp: array<u32>','var<storage,read> inp: array<vec4<u32>>')
    t=t.replace('var<storage,read_write> val: array<u32>','var<storage,read_write> val: array<vec4<u32>>')
    t=t.replace('var<storage,read_write> st: array<u32>','var<storage,read_write> st: array<vec4<u32>>')
    t=t.replace('var<storage,read_write> outp: array<u32>','var<storage,read_write> outp: array<vec4<u32>>')
    t=t.replace('val[w] = 0u;','val[w] = vec4<u32>(0u);').replace('val[W + w] = 0xffffffffu;','val[W + w] = vec4<u32>(0xffffffffu);')
    return t


def records(net):
    """Flat u32 pairs: NAND (a,b); LATCH (0x80000000|d, 0). Plus per-latch D list."""
    rec=np.zeros(2*len(net.records),dtype=np.uint32);latd=[]
    for i,r in enumerate(net.records):
        if r[0]==0:rec[2*i]=r[1];rec[2*i+1]=r[2]
        else:rec[2*i]=0x80000000|r[1];latd.append(r[1])
    return rec,np.array(latd or [0],dtype=np.uint32),len(latd)


def load(path,n_in,n_out):
    return Netlist.decode(Path(path).read_bytes(),n_in,n_out)


class GPU:
    def __init__(self,vec=1):
        import wgpu
        self.vec=vec
        self.wgpu=wgpu
        self.adapter=wgpu.gpu.request_adapter_sync(power_preference='high-performance')
        lim=self.adapter.limits
        self.device=self.adapter.request_device_sync(required_limits={
            'max-storage-buffer-binding-size':lim['max-storage-buffer-binding-size'],
            'max-buffer-size':lim['max-buffer-size']})
        self.module=self.device.create_shader_module(code=shader(vec))
        self.pipeline=self.device.create_compute_pipeline(layout='auto',compute={'module':self.module,'entry_point':'main'})

    def run(self,net,inp,clocks_per_dispatch=None,state=None):
        """inp: uint32 [T, n_in, W] -> (outputs uint32 [T, n_out, W], final state [n_lat, W], timings)."""
        wgpu=self.wgpu;d=self.device;U=wgpu.BufferUsage
        T,n_in,W=inp.shape;assert n_in==net.n_in and W%self.vec==0
        rec,latd,n_lat=records(net);n_rec=len(net.records);nw=2+n_in+n_rec
        t0=time.perf_counter()
        b_rec=d.create_buffer_with_data(data=rec,usage=U.STORAGE)
        b_latd=d.create_buffer_with_data(data=latd,usage=U.STORAGE)
        b_val=d.create_buffer(size=4*nw*W,usage=U.STORAGE)
        st=np.zeros((max(n_lat,1),W),dtype=np.uint32) if state is None else state
        b_st=d.create_buffer_with_data(data=st,usage=U.STORAGE|U.COPY_SRC)  # also the 4-byte sync read after each dispatch
        b_out=d.create_buffer(size=4*T*net.n_out*W,usage=U.STORAGE|U.COPY_SRC)
        # Keep each dispatch well under the amdgpu ring timeout: ~5e11 lane-gates/s when the GPU
        # is full, ~1e7 records/s per invocation when it is not (single-stream replay).
        budget=min(5e11*0.5/(n_rec*W*32),1e7*0.5/n_rec)
        step=max(1,min(T,clocks_per_dispatch or T,int(budget)));kernel=0.0;upload=time.perf_counter()-t0
        for t in range(0,T,step):
            n=min(step,T-t)
            b_inp=d.create_buffer_with_data(data=np.ascontiguousarray(inp[t:t+n]),usage=U.STORAGE)
            params=np.array([n_in,n_rec,net.n_out,n_lat,W//self.vec,n,t,0],dtype=np.uint32)
            b_p=d.create_buffer_with_data(data=params,usage=U.UNIFORM)
            bg=d.create_bind_group(layout=self.pipeline.get_bind_group_layout(0),entries=[
                {'binding':0,'resource':{'buffer':b_rec}},{'binding':1,'resource':{'buffer':b_inp}},
                {'binding':2,'resource':{'buffer':b_val}},{'binding':3,'resource':{'buffer':b_st}},
                {'binding':4,'resource':{'buffer':b_out}},{'binding':5,'resource':{'buffer':b_latd}},
                {'binding':6,'resource':{'buffer':b_p}}])
            enc=d.create_command_encoder();cp=enc.begin_compute_pass()
            cp.set_pipeline(self.pipeline);cp.set_bind_group(0,bg);cp.dispatch_workgroups((W//self.vec+63)//64);cp.end()
            k=time.perf_counter();d.queue.submit([enc.finish()]);d.queue.read_buffer(b_st,0,4);kernel+=time.perf_counter()-k
        k=time.perf_counter()
        out=np.frombuffer(d.queue.read_buffer(b_out),dtype=np.uint32).reshape(T,net.n_out,W).copy()
        fin=np.frombuffer(d.queue.read_buffer(b_st),dtype=np.uint32).reshape(-1,W)[:n_lat].copy()
        download=time.perf_counter()-k
        return out,fin,dict(upload_s=upload,kernel_s=kernel,download_s=download,total_s=time.perf_counter()-t0)


def ref_lib():
    so=HERE/'build'/'ref.so';so.parent.mkdir(exist_ok=True)
    if not so.exists() or so.stat().st_mtime<(HERE/'ref.c').stat().st_mtime:
        subprocess.run(['cc','-O3','-march=native','-fopenmp','-shared','-fPIC',str(HERE/'ref.c'),'-o',str(so)],check=True)
    lib=ct.CDLL(str(so));P=ct.c_void_p;u=ct.c_uint32
    lib.ref_run.argtypes=[P,u,u,u,P,u,u,u,P,P,P];return lib


def ref_run(lib,net,inp32):
    """Same semantics on CPU (64 lanes/word). inp32 uint32 [T,n_in,W32], W32 even."""
    T,n_in,W32=inp32.shape;assert W32%2==0
    inp=np.ascontiguousarray(inp32).view(np.uint64);W=W32//2
    rec,latd,n_lat=records(net)
    st=np.zeros((max(n_lat,1),W),dtype=np.uint64);out=np.zeros((T,net.n_out,W),dtype=np.uint64)
    t=time.perf_counter()
    lib.ref_run(rec.ctypes.data,net.n_in,len(net.records),net.n_out,latd.ctypes.data,n_lat,W,T,inp.ctypes.data,st.ctypes.data,out.ctypes.data)
    s=time.perf_counter()-t
    return out.view(np.uint32),st[:n_lat].view(np.uint32),s


def golden_lanes(net,inp32,lanes):
    """Official golden.Netlist.step_simd on the first `lanes` lanes, clock by clock."""
    T=inp32.shape[0];state=[b'']*lanes;outs=[]
    for t in range(T):
        x=[bytes((int(inp32[t,i,l//32])>>(l%32))&1 for i in range(net.n_in)) for l in range(lanes)]
        res=net.step_simd(state,x);state=[s for s,_ in res];outs.append([y for _,y in res])
    return outs,state


def lane_bits(arr,t,lanes):
    """arr uint32 [T,n,W] -> per-lane bytes of length n for clock t."""
    return [bytes((int(arr[t,i,l//32])>>(l%32))&1 for i in range(arr.shape[1])) for l in range(lanes)]
