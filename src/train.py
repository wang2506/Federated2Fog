import numpy as np
import torch
import torch.optim as optim


def get_model_weights(model, scaling_factor=1):
    if scaling_factor == 1:
        return model.state_dict()
    else:
        weights = model.state_dict()
        for key, val in weights.items():
            weights[key] = val*scaling_factor
        return weights


def add_model_weights(weights1, weights2):
    for key, val in weights2.items():
        weights1[key] += val

    return weights1


# Train using the fog aggregation
def fog_train(args, model, fog_graph, nodes, X_trains, y_trains, device, epoch):
    model.train()

    worker_data = {}
    worker_targets = {}
    worker_num_samples = {}
    worker_models = {}
    worker_optims = {}
    worker_losses = {}

    # send data, model to workers
    # setup optimizer for each worker

    workers = [_ for _ in nodes.keys() if 'L0' in _]
    for w, x, y in zip(workers, X_trains, y_trains):
        worker_data[w] = x.send(nodes[w])
        worker_targets[w] = y.send(nodes[w])
        worker_num_samples[w] = x.shape[0]

    for w in workers:
        worker_models[w] = model.copy().send(nodes[w])
        worker_optims[w] = optim.SGD(
            params=worker_models[w].parameters(), lr=args.lr)

        data = worker_data[w]
        target = worker_targets[w]
        data, target = data.to(device), target.to(device)
        worker_optims[w].zero_grad()
        output = worker_models[w](data)
        loss = F.nll_loss(output, target)
        loss.backward()
        worker_optims[w].step()
        worker_losses[w] = loss.get().data

    for l in range(1, len(args.num_clusters)+1):
        aggregators = [_ for _ in nodes.keys() if 'L{}'.format(l) in _]
        for a in aggregators:
            worker_models[a] = model.copy().send(nodes[a])
            worker_num_samples[a] = 1
            children = fog_graph[a]

            for child in children:
                worker_models[child].move(nodes[a])

            with torch.no_grad():
                weighted_models = [get_model_weights(
                    worker_models[_], worker_num_samples[_])for _ in children]
                model_sum = weighted_models[0]
                for m in weighted_models[1:]:
                    model_sum = add_model_weights(model_sum, m)
                worker_models[a].load_state_dict(model_sum)

    assert len(aggregators) == 1
    master = get_model_weights(worker_models[aggregators[0]].get(),
                               1/args.num_train)
    model.load_state_dict(master)

    loss = np.array([_.cpu().numpy() for dump, _ in worker_losses.items()])
    print('Train Epoch: {} \tLoss: {:.6f} +- {:.6f}'.format(
        epoch,
        loss.mean(), loss.std()
    ))


# Test
def test(args, model, device, test_loader, best):
    model.eval()
    test_loss = 0
    correct = 0
    with torch.no_grad():
        for data, target in test_loader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            test_loss += F.nll_loss(output, target, reduction='sum').item()
            pred = output.argmax(1, keepdim=True)
            correct += pred.eq(target.view_as(pred)).sum().item()

    test_loss /= len(test_loader.dataset)
    accuracy = correct / len(test_loader.dataset)
    if accuracy > best:
        best = accuracy

    print('\nTest set: Average loss: {:.4f}, Accuracy: {}/{} ({:.2f}%) ==> '
          '{:.2f}%'.format(
              test_loss, correct, len(test_loader.dataset),
              100.*accuracy, 100.*best))

    return best
