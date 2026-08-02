import { Component } from 'react'
import type { ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
}

/**
 * 兜底错误边界：页面 JS 运行时报错时，显示可恢复的提示页，
 * 而不是整个白屏。点击"重新加载"恢复。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  componentDidCatch(error: unknown) {
    // 记录到控制台，方便排查（看板无后端日志通道）
    console.error('[ErrorBoundary]', error)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="empty"
          style={{ padding: 48, textAlign: 'center' }}
        >
          <div style={{ fontSize: 28 }}>😵</div>
          <div style={{ marginTop: 8 }}>页面出错了</div>
          <button
            className="btn"
            style={{ marginTop: 12 }}
            onClick={() => {
              this.setState({ hasError: false })
              window.location.reload()
            }}
          >
            重新加载
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
