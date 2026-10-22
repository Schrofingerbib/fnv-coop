// Off-main-thread helpers. synccore itself stays single-threaded: sessions only ever touch their state from the thread that
// calls tick()/commit(). Heavy PURE work (hashing a world, diffing baselines) is snapshotted on the main thread, handed to an
// IWorker as a closure over immutable copies, and its result comes back through a queue that the session drains inside tick().
// With no worker set the work runs inline, so simulations stay deterministic.
//
// Rules for work closures: capture only copies / shared_ptr<const ...>; never touch a session, a transport or the engine;
// the process-wide volatile-keys list and Mutations are read-only while sessions run.
#pragma once
#include <atomic>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include "fnvcoop/mutations.h"

namespace fnvcoop {

class IWorker {
public:
    virtual ~IWorker() = default;
    virtual void post(std::function<void()> job) = 0;   // may be called from the main thread only
};

// One background thread, FIFO.
class ThreadWorker : public IWorker {
public:
    ThreadWorker();
    ~ThreadWorker() override;   // finishes queued jobs, then joins
    ThreadWorker(const ThreadWorker&) = delete;
    ThreadWorker& operator=(const ThreadWorker&) = delete;
    void post(std::function<void()> job) override;
    size_t pending() const;       // queued + running
    void waitIdle();              // blocks until everything posted so far has run (tests)
    std::thread::native_handle_type nativeHandle() { return th_.native_handle(); }   // the plugin's hot reload adopts this thread
private:
    void run();
    mutable std::mutex m_;
    std::condition_variable cv_, idleCv_;
    std::vector<std::function<void()>> q_;
    bool stop_ = false, busy_ = false;
    std::thread th_;
};

// Results returning to the main thread.
class MainQueue {
public:
    void push(std::function<void()> f);
    size_t drain();                                  // runs queued completions on the calling (main) thread
    int outstanding() const { return outstanding_.load(); }
    void began() { ++outstanding_; }
    void finished() { --outstanding_; }
private:
    std::mutex m_;
    std::vector<std::function<void()>> q_;
    std::atomic<int> outstanding_{0};
};

// Run `work` (returns R) on the worker, then `done(R)` on the main thread during the next drain(). Inline if w == nullptr.
template <class R, class Work, class Done>
void runAsync(IWorker* w, const std::shared_ptr<MainQueue>& q, Work work, Done done) {
    if (!w) { done(work()); return; }
    q->began();
    auto doneP = std::make_shared<Done>(std::move(done));
    auto workP = std::make_shared<Work>(std::move(work));
    // The completion is queued ON q and only ever runs inside q's drain(), so it must not own q: a strong capture is a reference cycle
    // (q -> its queue -> the closure -> q) and a session destroyed while a result is still queued leaked the queue and the result.
    // Only the job posted to the worker holds q strongly (it keeps q alive until the completion is queued); the completion gets a raw pointer.
    const bool holdQueue = mutations().asyncDoneHoldsQueue;   // test twin: the old, cyclic capture
    w->post([q, doneP, workP, holdQueue]() {
        auto r = std::make_shared<R>((*workP)());
        MainQueue* raw = q.get();
        std::shared_ptr<MainQueue> cycle = holdQueue ? q : nullptr;
        q->push([raw, cycle, doneP, r]() { (*doneP)(std::move(*r)); raw->finished(); });
    });
}

// ---- definitions (header-only: no extra source file to add to anyone's build) ----
inline ThreadWorker::ThreadWorker() : th_([this] { run(); }) {}

inline ThreadWorker::~ThreadWorker() {
    {
        std::lock_guard<std::mutex> l(m_);
        stop_ = true;
    }
    cv_.notify_all();
    th_.join();
}

inline void ThreadWorker::post(std::function<void()> job) {
    {
        std::lock_guard<std::mutex> l(m_);
        q_.push_back(std::move(job));
    }
    cv_.notify_one();
}

inline size_t ThreadWorker::pending() const {
    std::lock_guard<std::mutex> l(m_);
    return q_.size() + (busy_ ? 1 : 0);
}

inline void ThreadWorker::waitIdle() {
    std::unique_lock<std::mutex> l(m_);
    idleCv_.wait(l, [this] { return q_.empty() && !busy_; });
}

inline void ThreadWorker::run() {
    for (;;) {
        std::function<void()> job;
        {
            std::unique_lock<std::mutex> l(m_);
            cv_.wait(l, [this] { return stop_ || !q_.empty(); });
            if (q_.empty()) return;   // stop_ and drained
            job = std::move(q_.front());
            q_.erase(q_.begin());
            busy_ = true;
        }
        job();
        {
            std::lock_guard<std::mutex> l(m_);
            busy_ = false;
        }
        idleCv_.notify_all();
    }
}

inline void MainQueue::push(std::function<void()> f) {
    std::lock_guard<std::mutex> l(m_);
    q_.push_back(std::move(f));
}

inline size_t MainQueue::drain() {
    std::vector<std::function<void()>> local;
    {
        std::lock_guard<std::mutex> l(m_);
        local.swap(q_);
    }
    for (auto& f : local) f();
    return local.size();
}

}  // namespace fnvcoop
