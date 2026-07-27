import { useEffect, useState } from 'react'
import { Routes, Route, NavLink } from 'react-router-dom'
import Timeline from './pages/Timeline'
import Memory from './pages/Memory'
import Projects from './pages/Projects'
import { fetchProposals } from './api/client'

export default function App() {
  const [pendingCount, setPendingCount] = useState(0)

  useEffect(() => {
    let active = true
    function poll() {
      fetchProposals('pending')
        .then((d) => {
          if (active) setPendingCount(d.pending_count)
        })
        .catch(() => {})
    }
    poll()
    const iv = setInterval(poll, 30_000)
    return () => {
      active = false
      clearInterval(iv)
    }
  }, [])

  return (
    <>
      <nav className="navbar">
        <div className="brand">
          <span className="dot" />
          MIND · 记忆看板
        </div>
        <NavLink to="/" end className="nav-link">
          时间线
        </NavLink>
        <NavLink to="/memory" className="nav-link">
          记忆审核
          {pendingCount > 0 && (
            <span style={{ marginLeft: 4, opacity: 0.7 }}>
              ({pendingCount})
            </span>
          )}
        </NavLink>
        <NavLink to="/projects" className="nav-link">
          项目管理
        </NavLink>
        <div className="spacer" />
      </nav>

      <Routes>
        <Route path="/" element={<Timeline />} />
        <Route path="/memory" element={<Memory />} />
        <Route path="/projects" element={<Projects />} />
      </Routes>
    </>
  )
}
