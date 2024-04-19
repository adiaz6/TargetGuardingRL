import numpy as np
import torch
from collections import deque, namedtuple
import random
import math
from itertools import count

# https://github.com/pytorch/tutorials/blob/main/intermediate_source/reinforcement_q_learning.py

class ReplayMemory:
    def __init__(self, capacity):
        self.memory = deque([], maxlen=capacity)

    def push(self, Transition, *args):
        """Save a transition"""
        self.memory.append(Transition(*args))

    def sample(self, batch_size):
        return random.sample(self.memory, batch_size)

    def __len__(self):
        return len(self.memory)
    
class DQN(torch.nn.Module):
    def __init__(self, 
                 n_observations, # state dimensions
                 n_actions, # number of actions
                 lr=1e-4 # learning rate
                 ):
        super(DQN, self).__init__()
        self.layer1 = torch.nn.Linear(n_observations, 128)
        self.layer2 = torch.nn.Linear(128, 128)
        self.layer3 = torch.nn.Linear(128, n_actions)

        self.optimizer = torch.optim.AdamW(self.parameters(), lr=lr, amsgrad=True)

    # Called with either one element to determine next action, or a batch
    # during optimization. Returns tensor([[left0exp,right0exp]...]).
    def forward(self, x):
        x = torch.nn.functional.relu(self.layer1(x))
        x = torch.nn.functional.relu(self.layer2(x))
        return self.layer3(x)
    
    def optimize_model(self, 
                       memory,  # Replay memory 
                       batch_size, # batch size
                       device, # device to use
                       gamma, # discount factor
                       target_net, # target network
                       Transition # named tuple for storing transitions
                       ):
        if len(memory) < batch_size:
            return
        
        transitions = memory.sample(batch_size)
        
        # Transpose the batch (see https://stackoverflow.com/a/19343/3343043 for
        # detailed explanation). This converts batch-array of Transitions
        # to Transition of batch-arrays.
        batch = Transition(*zip(*transitions))

        # Compute a mask of non-final states and concatenate the batch elements
        # (a final state would've been the one after which simulation ended)
        non_final_mask = torch.tensor(tuple(map(lambda s: s is not None,
                                            batch.next_state)), device=device, dtype=torch.bool)
        
        non_final_next_states = torch.cat([s for s in batch.next_state
                                                    if s is not None])
        
        state_batch = torch.cat(batch.state)
        action_batch = torch.cat(batch.action)
        reward_batch = torch.cat(batch.reward)

        # Compute Q(s_t, a) - the model computes Q(s_t), then we select the
        # columns of actions taken. These are the actions which would've been taken
        # for each batch state according to policy_net
        state_action_values = self(state_batch).gather(1, action_batch)

        # Compute V(s_{t+1}) for all next states.
        # Expected values of actions for non_final_next_states are computed based
        # on the "older" target_net; selecting their best reward with max(1).values
        # This is merged based on the mask, such that we'll have either the expected
        # state value or 0 in case the state was final.
        next_state_values = torch.zeros(batch_size, device=device)

        with torch.no_grad():
            next_state_values[non_final_mask] = target_net(non_final_next_states).max(1).values

        # Compute the expected Q values
        expected_state_action_values = (next_state_values * gamma) + reward_batch

        # Compute Huber loss
        criterion = torch.nn.SmoothL1Loss()
        loss = criterion(state_action_values, expected_state_action_values.unsqueeze(1))

        # Optimize the model
        self.optimizer.zero_grad()
        loss.backward()

        # In-place gradient clipping
        torch.nn.utils.clip_grad_value_(self.parameters(), 100)
        self.optimizer.step()

class EpsilonGreedyPolicy:
    def __init__(self,
                 eps_end,
                 eps_start,
                 eps_decay,
                 n_actions,
                 device):
        self.steps_done = 0
        self.eps_end = eps_end
        self.eps_start = eps_start
        self.eps_decay = eps_decay
        self.n_actions = n_actions
        self.device = device

    # Epsilon greedy selection
    def select_action(self, state, policy_net):
        sample = random.random()
        eps_threshold = self.eps_end + (self.eps_start - self.eps_end) * \
            math.exp(-1. * self.steps_done / self.eps_decay)
        
        self.steps_done += 1

        if sample > eps_threshold:
            with torch.no_grad():
                # t.max(1) will return the largest column value of each row.
                # second column on max result is index of where max element was
                # found, so we pick action with the larger expected reward.
                return policy_net(state).max(1).indices.view(1, 1)
        else:
            return torch.tensor([[random.randint(0, self.n_actions - 1)]], device=self.device, dtype=torch.long)


def dqn(env, 
        eps_start=0.9,  # start value for epsilon
        eps_end=0.05,   # end value for epsilon
        eps_decay=1000, # decay rate for epsilon-greedy
        episodes=10000, # number of episodes
        gamma=0.99, # discount factor
        N=10000,   # Replay memory max size,
        tau=0.005, # Update rate for target network
        batch_size=128, # minibatch size 
        lr=1e-4 # learning rate
        ):
    # if GPU is to be used
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Get number of actions from gym action space
    n_actions = env.state_dims
    n_observations = env.action_dims

    policy_net = DQN(n_observations, n_actions).to(device)
    target_net = DQN(n_observations, n_actions).to(device)
    target_net.load_state_dict(policy_net.state_dict())

    policy = EpsilonGreedyPolicy(eps_end, eps_start, eps_decay, n_actions, device)

    # Initialize replay memory with size 10000
    Transition = namedtuple('Transition', ('state', 'action', 'next_state', 'reward'))
    memory = ReplayMemory(N)

    for i_episode in range(episodes):
        # Initialize the environment and get its state
        state = env.reset()
        state = torch.tensor(state, dtype=torch.float32, device=device).unsqueeze(0)

        for t in count():
            action = policy.select_action(state, policy_net)
            observation, reward, done, info = env.step(action.item())
            reward = torch.tensor([reward], device=device)

            if done:
                next_state = None
            else:
                next_state = torch.tensor(observation, dtype=torch.float32, device=device).unsqueeze(0)

            # Store the transition in memory
            memory.push(Transition, state, action, next_state, reward)

            # Move to the next state
            state = next_state

            # Perform one step of the optimization (on the policy network)
            policy_net.optimize_model(memory, batch_size, device, gamma, target_net, Transition)

            # Soft update of the target network's weights
            # θ′ ← τ θ + (1 − τ) θ′
            target_net_state_dict = target_net.state_dict()
            policy_net_state_dict = policy_net.state_dict()

            for key in policy_net_state_dict:
                target_net_state_dict[key] = policy_net_state_dict[key] * tau + target_net_state_dict[key] * (1 - tau)

            target_net.load_state_dict(target_net_state_dict)

            if done:
                break

    # Data to save 
    """
    rewards = deque() # Save rewards for each episode
    states = deque() # Save states for each episode
    termination_condition = deque() # Save termination conditions for each episode
    """