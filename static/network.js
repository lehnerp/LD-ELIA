/* Shared graph aggregation, independent of the DOM. */
(function (root) {
  const rank = {Confirmed:5,Suspicious:4,Investigating:3,Review:2,Excluded:1,Clear:1,Dismissed:0};
  function build(events, focus=null, limit=24) {
    const pairs=new Map();
    for(const e of events) {
      if(focus && (focus.kind==='user'?e.user.toLowerCase()!==focus.value.toLowerCase():e.ip!==focus.value)) continue;
      const key=JSON.stringify([e.user.toLowerCase(),e.ip]);
      if(!pairs.has(key)) pairs.set(key,{key,user:e.user,ip:e.ip,count:0,failed:0,flagged:0,countries:new Set(),top:e});
      const edge=pairs.get(key); edge.count++; edge.failed+=e.status==='Failure';
      edge.flagged+=['Confirmed','Suspicious','Investigating'].includes(e.label);edge.countries.add(e.country);
      if((rank[e.label]||0)>(rank[edge.top.label]||0) || (rank[e.label]===rank[edge.top.label] && (e.score>edge.top.score || (e.score===edge.top.score && e.time>edge.top.time)))) edge.top=e;
    }
    const edges=[...pairs.values()].sort((a,b)=>(rank[b.top.label]-rank[a.top.label])||(b.top.score-a.top.score)||(b.count-a.count)).slice(0,limit);
    const nodes=kind=>{const map=new Map();for(const edge of edges){const value=edge[kind],key=kind==='user'?value.toLowerCase():value;if(!map.has(key))map.set(key,{kind,value,key,count:0,edges:[],top:edge.top});const n=map.get(key);n.count+=edge.count;n.edges.push(edge);if((rank[edge.top.label]||0)>(rank[n.top.label]||0)||edge.top.score>n.top.score)n.top=edge.top;}return [...map.values()].sort((a,b)=>b.top.score-a.top.score||b.count-a.count)};
    return {edges,users:nodes('user'),ips:nodes('ip'),total:pairs.size,shown:edges.length};
  }
  if(typeof module!=='undefined' && module.exports)module.exports={build};
  root.Network={build};
})(typeof window!=='undefined'?window:globalThis);
