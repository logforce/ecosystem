// SPDX-License-Identifier: Apache-2.0
//! FIFO within a session, round-robin between sessions with queued work.
use std::collections::{HashMap, VecDeque};

pub(crate) struct FairQueue<T> {
    ready: VecDeque<u64>,
    sessions: HashMap<u64, VecDeque<T>>,
}

impl<T> Default for FairQueue<T> {
    fn default() -> Self {
        Self {
            ready: VecDeque::new(),
            sessions: HashMap::new(),
        }
    }
}

impl<T> FairQueue<T> {
    pub(crate) fn is_empty(&self) -> bool {
        self.ready.is_empty()
    }

    pub(crate) fn push(&mut self, owner: u64, item: T) {
        let queue = self.sessions.entry(owner).or_default();
        if queue.is_empty() {
            self.ready.push_back(owner);
        }
        queue.push_back(item);
    }

    pub(crate) fn pop(&mut self) -> Option<T> {
        let owner = self.ready.pop_front()?;
        let queue = self
            .sessions
            .get_mut(&owner)
            .expect("ready session has a queue");
        let item = queue.pop_front().expect("ready queue is nonempty");
        if queue.is_empty() {
            self.sessions.remove(&owner);
        } else {
            self.ready.push_back(owner);
        }
        Some(item)
    }

    pub(crate) fn remove_owner(&mut self, owner: u64) {
        self.sessions.remove(&owner);
        self.ready.retain(|id| *id != owner);
    }

    pub(crate) fn retain_owner(&mut self, owner: u64, keep: impl FnMut(&T) -> bool) {
        if let Some(queue) = self.sessions.get_mut(&owner) {
            queue.retain(keep);
            if queue.is_empty() {
                self.remove_owner(owner);
            }
        }
    }

    pub(crate) fn clear(&mut self) {
        self.ready.clear();
        self.sessions.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_robin_preserves_session_fifo_and_does_not_starve_third_session() {
        let mut queue = FairQueue::default();
        for owner in 1..=3 {
            for n in 0..3 {
                queue.push(owner, (owner, n));
            }
        }
        let mut observed = Vec::new();
        while let Some(item) = queue.pop() {
            observed.push(item);
        }
        assert_eq!(
            observed,
            vec![
                (1, 0),
                (2, 0),
                (3, 0),
                (1, 1),
                (2, 1),
                (3, 1),
                (1, 2),
                (2, 2),
                (3, 2)
            ]
        );
        assert!(queue.is_empty());
    }

    #[test]
    fn cancellation_disconnect_reentry_and_clear_leave_no_duplicate_turns() {
        let mut queue = FairQueue::default();
        queue.push(1, 10);
        queue.push(1, 11);
        queue.push(2, 20);
        queue.push(3, 30);
        queue.retain_owner(1, |item| *item == 11);
        queue.remove_owner(2);
        queue.retain_owner(3, |_| false);
        queue.push(2, 21);
        queue.push(2, 22);
        assert_eq!(queue.pop(), Some(11));
        assert_eq!(queue.pop(), Some(21));
        queue.push(1, 12);
        assert_eq!(queue.pop(), Some(22));
        assert_eq!(queue.pop(), Some(12));
        queue.push(1, 13);
        queue.clear();
        assert!(queue.is_empty());
        assert_eq!(queue.pop(), None);
    }

    #[test]
    fn deterministic_queue_churn_preserves_one_turn_per_nonempty_session() {
        let mut queue = FairQueue::default();
        let mut seed = 17u64;
        let mut last = [0; 8];
        for item in 1..=20_000u64 {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            let owner = seed % 8;
            match (seed >> 8) % 5 {
                0 | 1 => queue.push(owner, (owner, item)),
                2 => {
                    if let Some((owner, value)) = queue.pop() {
                        assert!(value > last[owner as usize]);
                        last[owner as usize] = value;
                    }
                }
                3 => queue.remove_owner(owner),
                _ => queue.retain_owner(owner, |(_, value)| value % 2 == 0),
            }
            assert_eq!(queue.ready.len(), queue.sessions.len());
            let unique: std::collections::HashSet<_> = queue.ready.iter().collect();
            assert_eq!(unique.len(), queue.ready.len());
            for owner in &queue.ready {
                assert!(!queue.sessions[owner].is_empty());
            }
        }
    }
}
