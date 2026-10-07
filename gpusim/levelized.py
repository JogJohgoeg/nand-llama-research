#!/usr/bin/env python3
"""Levelized GPU simulation: one workgroup per 32-lane word, its threads split each gate level.

gpusim.py gives each invocation a whole timeline, so a 600k-record graph runs ~17 clocks/s per
lane word. Here the NAND records are sorted by logic level; for every clock a workgroup loads
inputs and latch outputs, evaluates level after level (threads strided over the gates of a level,
storageBarrier between levels (workgroupBarrier does not order storage memory)), then captures the latches. Values are word-major
(val[word * nw + wire]) so a workgroup touches only its own slice. Semantics are exactly
golden.Netlist.step (all state zero at reset). Inputs: a per-clock prefix [Tin, n_in, W]; clocks
after Tin repeat the last prefix row (hold). Only the outputs of the last clock are returned.
"""
from pathlib import Path
import sys,time
import numpy as np
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
sys.path[:0]=[str(ROOT)]
from golden import Netlist

WG=64
MAXLEV=1024
SHADER='''
struct P { n_in:u32, n_rec:u32, n_out:u32, n_lat:u32, W:u32, T:u32, Tin:u32, t0:u32, n_lev:u32, nw:u32, last:u32, pad:u32 };
@group(0) @binding(0) var<storage,read> g: array<u32>;        // per gate: a, b, out (level order)
@group(0) @binding(1) var<storage,read> inp: array<u32>;      // [Tin][n_in][W]
@group(0) @binding(2) var<storage,read_write> val: array<u32>; // [W][nw]
@group(0) @binding(3) var<storage,read_write> st: array<u32>;  // [W][n_lat]
@group(0) @binding(4) var<storage,read_write> outp: array<u32>;// [W][n_out]
@group(0) @binding(5) var<storage,read> lat: array<u32>;      // per latch: output wire, D wire
@group(0) @binding(6) var<uniform> p: P;
@group(0) @binding(7) var<uniform> lev: array<vec4<u32>, 256>; // gate index where each level starts (n_lev+1 entries)
fn lv(i:u32) -> u32 { return lev[i / 4u][i % 4u]; }
@compute @workgroup_size(64)
fn main(@builtin(workgroup_id) wid: vec3<u32>, @builtin(local_invocation_index) lid: u32) {
  let w = wid.x;
  let b = w * p.nw;
  for (var t = 0u; t < p.T; t = t + 1u) {
    let ti = min(p.t0 + t, p.Tin - 1u);
    if (lid == 0u) { val[b] = 0u; val[b + 1u] = 0xffffffffu; }
    for (var i = lid; i < p.n_in; i = i + 64u) { val[b + 2u + i] = inp[(ti * p.n_in + i) * p.W + w]; }
    for (var j = lid; j < p.n_lat; j = j + 64u) { val[b + lat[2u * j]] = st[w * p.n_lat + j]; }
    storageBarrier();
    for (var l = 0u; l < p.n_lev; l = l + 1u) {
      let e = lv(l + 1u);
      for (var k = lv(l) + lid; k < e; k = k + 64u) {
        val[b + g[3u * k + 2u]] = ~(val[b + g[3u * k]] & val[b + g[3u * k + 1u]]);
      }
      storageBarrier();
    }
    for (var j = lid; j < p.n_lat; j = j + 64u) { st[w * p.n_lat + j] = val[b + lat[2u * j + 1u]]; }
    if (p.last == 1u && t + 1u == p.T) {
      for (var o = lid; o < p.n_out; o = o + 64u) { outp[w * p.n_out + o] = val[b + p.nw - p.n_out + o]; }
    }
    storageBarrier();
  }
}
'''


def levelize(net):
    ni=net.n_in;lvl={0:0,1:0};gates=[];lats=[]
    for i in range(ni):lvl[2+i]=0
    for i,r in enumerate(net.records):
        w=2+ni+i
        if r[0]==0:
            l=1+max(lvl[r[1]],lvl[r[2]]);lvl[w]=l;gates.append((l,r[1],r[2],w))
        else:lvl[w]=0;lats.append((w,r[1]))
    gates.sort(key=lambda x:x[0]);n_lev=gates[-1][0] if gates else 0
    starts=[0]*(n_lev+2);c=0
    for l in range(1,n_lev+1):
        starts[l-1]=c
        while c<len(gates) and gates[c][0]==l:c+=1
    starts[n_lev]=c
    g=np.array([[a,b,o] for _,a,b,o in gates],dtype=np.uint32).reshape(-1)
    lat=np.array([[o,d] for o,d in lats] or [[0,0]],dtype=np.uint32).reshape(-1)
    assert n_lev<MAXLEV
    return g,lat,len(lats),n_lev,np.array(starts[:n_lev+1]+[0]*(MAXLEV-n_lev-1),dtype=np.uint32)


class GPU:
    def __init__(self):
        import wgpu
        self.wgpu=wgpu
        ad=wgpu.gpu.request_adapter_sync(power_preference='high-performance');lim=ad.limits
        self.device=ad.request_device_sync(required_limits={'max-storage-buffer-binding-size':lim['max-storage-buffer-binding-size'],'max-buffer-size':lim['max-buffer-size']})
        self.pipeline=self.device.create_compute_pipeline(layout='auto',compute={'module':self.device.create_shader_module(code=SHADER),'entry_point':'main'})

    def run(self,net,inp,T,clocks_per_dispatch=500,progress=None,debug=False):
        """inp uint32 [Tin, n_in, W]; runs T clocks (inputs held after Tin); returns (outputs [n_out, W], state, timings)."""
        wgpu=self.wgpu;d=self.device;U=wgpu.BufferUsage
        Tin,n_in,W=inp.shape;assert n_in==net.n_in
        g,lat,n_lat,n_lev,starts=levelize(net);nw=2+n_in+len(net.records)
        t0=time.perf_counter()
        bg_=d.create_buffer_with_data(data=g,usage=U.STORAGE);b_lat=d.create_buffer_with_data(data=lat,usage=U.STORAGE)
        b_inp=d.create_buffer_with_data(data=np.ascontiguousarray(inp),usage=U.STORAGE)
        b_val=d.create_buffer(size=4*nw*W,usage=U.STORAGE|U.COPY_SRC)
        b_st=d.create_buffer_with_data(data=np.zeros(max(n_lat,1)*W,dtype=np.uint32),usage=U.STORAGE|U.COPY_SRC)
        b_out=d.create_buffer(size=4*net.n_out*W,usage=U.STORAGE|U.COPY_SRC)
        b_lev=d.create_buffer_with_data(data=starts,usage=U.UNIFORM)
        kern=0.0
        for t in range(0,T,clocks_per_dispatch):
            n=min(clocks_per_dispatch,T-t)
            params=np.array([n_in,len(net.records),net.n_out,n_lat,W,n,Tin,t,n_lev,nw,int(t+n==T),0],dtype=np.uint32)
            b_p=d.create_buffer_with_data(data=params,usage=U.UNIFORM)
            bg=d.create_bind_group(layout=self.pipeline.get_bind_group_layout(0),entries=[
                {'binding':0,'resource':{'buffer':bg_}},{'binding':1,'resource':{'buffer':b_inp}},{'binding':2,'resource':{'buffer':b_val}},
                {'binding':3,'resource':{'buffer':b_st}},{'binding':4,'resource':{'buffer':b_out}},{'binding':5,'resource':{'buffer':b_lat}},
                {'binding':6,'resource':{'buffer':b_p}},{'binding':7,'resource':{'buffer':b_lev}}])
            enc=d.create_command_encoder();cp=enc.begin_compute_pass();cp.set_pipeline(self.pipeline);cp.set_bind_group(0,bg)
            cp.dispatch_workgroups(W);cp.end()
            k=time.perf_counter();d.queue.submit([enc.finish()]);d.queue.read_buffer(b_st,0,4);kern+=time.perf_counter()-k
            if progress:progress(t+n,kern)
        out=np.frombuffer(d.queue.read_buffer(b_out),dtype=np.uint32).reshape(W,net.n_out).T.copy()
        fin=np.frombuffer(d.queue.read_buffer(b_st),dtype=np.uint32).reshape(W,-1)[:,:n_lat].T.copy()
        info=dict(levels=n_lev,kernel_s=kern,total_s=time.perf_counter()-t0,clocks_per_s=T/max(kern,1e-9))
        if debug:info['val']=np.frombuffer(d.queue.read_buffer(b_val),dtype=np.uint32).reshape(W,nw).copy()
        return out,fin,info
